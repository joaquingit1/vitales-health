"""Command line: validate the wiring, dump a scenario, or feed the hub with SIM or REPLAY readings.

The sim and replay commands are the interim of E23's `sim` and `replay` adapters (E23 design 5.2).
"""

import argparse
import json
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from crew_health_sim_habitat.app import Loaded, load_all
from crew_health_sim_habitat.errors import ConfigError, SimulatorError
from crew_health_sim_habitat.feeder import Feeder, FeederSettings, SequenceStore, UrllibTransport, drain
from crew_health_sim_habitat.payload import format_instant
from crew_health_sim_habitat.replay import ReplayCounts, ReplayCursor, dataset_rows, feed_replay
from crew_health_sim_habitat.runner import RunReport, WireMapper, build_devices, feed_scenario, simulate
from crew_health_sim_habitat.wiring import unmapped_outputs

STATE_DIR = Path("simulators/habitat/.state")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-health-sim-habitat")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--catalog", type=Path, default=None, help="override the sensor catalog path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="load and check every document, list unmapped outputs")
    dump = sub.add_parser("dump", help="print a scenario's device readings as JSON lines (no hub)")
    dump.add_argument("--scenario", required=True)
    dump.add_argument("--start", default="2026-11-14T00:00:00Z")
    defaults = FeederSettings()
    for name in ("sim", "replay"):
        cmd = sub.add_parser(name, help=f"post {name.upper()} readings to the hub")
        cmd.add_argument("--hub-url", default=None)
        cmd.add_argument("--state-dir", type=Path, default=None)
        cmd.add_argument("--health-port", type=int, default=None)
        cmd.add_argument("--queue-max", type=int, default=defaults.queue_max_readings)
        cmd.add_argument("--batch-max", type=int, default=defaults.batch_max_readings)
        cmd.add_argument("--outage-max-s", type=float, default=defaults.outage_max_s)
    sim = sub.choices["sim"]
    sim.add_argument("--scenario", required=True)
    sim.add_argument("--pace", choices=("realtime", "backfill"), default="realtime")
    replay = sub.choices["replay"]
    replay.add_argument("--feed-id", required=True)
    replay.add_argument(
        "--rows-per-second", type=float, default=0.0, help="0 sends as fast as the hub takes them"
    )
    return parser


def _state_dir(loaded: Loaded, args: argparse.Namespace) -> Path:
    state_dir: Path = args.state_dir if args.state_dir is not None else loaded.root / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _feeder(loaded: Loaded, mode: str, args: argparse.Namespace) -> Feeder:
    adapter = loaded.wiring.adapters[mode]
    token = os.environ.get(adapter.token_env, "")
    if not token:
        raise ConfigError(f"set {adapter.token_env} to the hub's token for {adapter.adapter_id}")
    settings = FeederSettings(
        queue_max_readings=args.queue_max, batch_max_readings=args.batch_max, outage_max_s=args.outage_max_s
    )
    state_dir = _state_dir(loaded, args)
    return Feeder(
        hub_url=args.hub_url or loaded.wiring.hub_url,
        adapter_id=adapter.adapter_id,
        token=token,
        settings=settings,
        transport=UrllibTransport(),
        now=time.monotonic,
        rng=random.Random(),
        sequences=SequenceStore(state_dir / f"{adapter.adapter_id}.json"),
    )


def _serve_health(port: int, adapter_id: str, feeder: Feeder) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            body = {
                "adapter_id": adapter_id,
                "clock": "unknown",
                "queue": "queue_full" if feeder.queue_full else "ok",
            }
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def _cmd_validate(loaded: Loaded) -> None:
    print(
        f"ok: {len(loaded.kinds)} device kinds, {len(loaded.scenarios)} scenarios, "
        f"{len(loaded.wiring.bindings)} bound outputs"
    )
    for device_id, output in unmapped_outputs(loaded.wiring, loaded.kinds):
        print(f"unmapped (no hub sensor type): {device_id}.{output}")


def _cmd_dump(loaded: Loaded, args: argparse.Namespace) -> None:
    scenario = loaded.scenario(args.scenario)
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    devices = build_devices(scenario, loaded.wiring, loaded.kinds)
    mapper = WireMapper(loaded.wiring, start, devices, FeederSettings().pressure_max_age_s)
    for tick in simulate(scenario, devices):
        for reading in tick.readings:
            binding = mapper.binding(reading)
            line = {
                "observed_at": format_instant(start + timedelta(seconds=reading.t_s)),
                "device_id": reading.device_id,
                "output": reading.output,
                "value": reading.quantity.value,
                "unit": reading.quantity.unit,
                "status": reading.status,
                "origin_mode": "SIM",
                "channel_id": None if binding is None else binding.channel_id,
            }
            print(json.dumps(line, ensure_ascii=False))


def _cmd_sim(loaded: Loaded, args: argparse.Namespace) -> RunReport:
    scenario = loaded.scenario(args.scenario)
    feeder = _feeder(loaded, "SIM", args)
    if args.health_port is not None:
        _serve_health(args.health_port, loaded.wiring.adapters["SIM"].adapter_id, feeder)
    now = datetime.now(UTC)
    realtime = args.pace == "realtime"
    start = now if realtime else now - timedelta(seconds=scenario.duration_s)
    origin = time.monotonic()
    wait: Callable[[float], None] = (
        (lambda t: time.sleep(max(0.0, origin + t - time.monotonic()))) if realtime else (lambda t: None)
    )
    devices = build_devices(scenario, loaded.wiring, loaded.kinds)
    mapper = WireMapper(loaded.wiring, start, devices, feeder.settings.pressure_max_age_s)
    hold = 1 if realtime else feeder.settings.batch_max_readings
    report = feed_scenario(simulate(scenario, devices), mapper, feeder, wait, pump_at_pending=hold)
    drain(feeder, time.sleep, lambda: None)
    print(json.dumps({"report": _report(report), "feeder": _stats(feeder)}))
    return report


def _cmd_replay(loaded: Loaded, args: argparse.Namespace) -> None:
    feed = loaded.replay_feed(args.feed_id)
    dataset = loaded.wiring.datasets[feed.dataset]
    counts = ReplayCounts()
    rows = dataset_rows(loaded.root, dataset, feed, counts)
    feeder = _feeder(loaded, "REPLAY", args)
    adapter_id = loaded.wiring.adapters["REPLAY"].adapter_id
    if args.health_port is not None:
        _serve_health(args.health_port, adapter_id, feeder)
    cursor = ReplayCursor(_state_dir(loaded, args) / f"{adapter_id}.replay.json")
    gap = 1.0 / args.rows_per_second if args.rows_per_second > 0 else 0.0
    run = feed_replay(
        rows,
        feed,
        loaded.wiring.quality_status["ok"],
        feeder,
        (cursor, ReplayCursor.key(feed, dataset)),
        time.sleep,
        gap,
    )
    summary = {
        "rows": counts.rows_read,
        "rejected_rows": counts.rows_rejected,
        "resumed_after_rows": run.resumed_after,
        "offered": run.offered,
        "feeder": _stats(feeder),
    }
    print(json.dumps(summary))


def _report(report: RunReport) -> dict[str, object]:
    return {
        "ticks": report.ticks,
        "readings": report.readings,
        "offered": report.offered,
        "skipped_queue_full": report.skipped_queue_full,
        "missing_pressure": report.missing_pressure,
        "unmapped": dict(sorted(report.unmapped.items())),
    }


def _stats(feeder: Feeder) -> dict[str, object]:
    s = feeder.stats
    return {
        "batches": s.batches_posted,
        "accepted": s.accepted,
        "duplicates": s.duplicates,
        "failed_attempts": s.attempts_failed,
        "rejected": dict(sorted(s.rejected.items())),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        loaded = load_all(args.repo_root.resolve(), catalog_path=args.catalog)
        if args.command == "validate":
            _cmd_validate(loaded)
        elif args.command == "dump":
            _cmd_dump(loaded, args)
        elif args.command == "sim":
            _cmd_sim(loaded, args)
        else:
            _cmd_replay(loaded, args)
    except SimulatorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
