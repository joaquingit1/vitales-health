"""Command line of the habitat adapters: `replay` and `sim` (E23 5.2).

    crew-health adapters replay --feed <feeds.json> [--feed-id <id>]
    crew-health adapters sim    --feed <feeds.json> [--feed-id <id>]

E20's shell owns the `crew-health` entry and its argument export; this module is the adapter side of that seam
(`main`). Demo replay and sim refuse to start unless the deployment profile is demo, and need no hub call to decide.
The token comes from the environment, never from an argument or a log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from crew_health_adapter_habitat.errors import AdapterError, QueueStorageError, TransportFault
from crew_health_adapter_habitat.feeds import Feed, load_feeds, select_feed
from crew_health_adapter_habitat.health import HealthServer, HealthState
from crew_health_adapter_habitat.http_client import HubClient, Sender
from crew_health_adapter_habitat.pipeline import Pipeline, RunSummary
from crew_health_adapter_habitat.ports import Clock, ClockState, ReadingSource, Settings, Sleeper
from crew_health_adapter_habitat.queue import DurableQueue
from crew_health_adapter_habitat.replay import CsvReplaySource
from crew_health_adapter_habitat.sim import build_sim_source

TOKEN_ENV = "CREW_HEALTH_ADAPTER_TOKEN"
PROFILE_ENV = "CREW_HEALTH_PROFILE"
EXIT_OK, EXIT_REFUSED, EXIT_TRANSPORT, EXIT_STORAGE = 0, 2, 3, 4


class SystemClock:
    """Wall time at the adapter's edge. `state` is unknown: the host time service is not queried, so no
    synchronization is ever claimed that was not observed."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def state(self) -> ClockState:
        return "unknown"


class SystemSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crew-health-adapters", description="Habitat REPLAY and SIM adapters"
    )
    parser.add_argument("kind", choices=["replay", "sim"])
    parser.add_argument("--feed", required=True, type=Path, help="feeds.json")
    parser.add_argument("--feed-id")
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--queue-file", required=True, type=Path)
    parser.add_argument("--settings", type=Path, help="JSON object of habitat_adapter.* settings")
    parser.add_argument("--profile", default=os.environ.get(PROFILE_ENV))
    parser.add_argument("--health-port", type=int, default=0)
    parser.add_argument("--restart", action="store_true", help="rewind this feed to its first row (explicit)")
    return parser


def _settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterError("settings unreadable") from error
    if not isinstance(values, dict):
        raise AdapterError("settings must be a JSON object")
    return Settings.from_mapping(values)


def make_source(feed: Feed, settings: Settings, clock: Clock, queue: DurableQueue) -> ReadingSource:
    if feed.kind == "replay":
        return CsvReplaySource(feed, settings)
    epoch_text = queue.meta("epoch:" + feed.feed_id, lambda: clock.now().isoformat())
    return build_sim_source(feed, clock, datetime.fromisoformat(epoch_text))


def run_feed(
    feed: Feed,
    *,
    client: httpx.Client,
    token: str,
    queue: DurableQueue,
    settings: Settings,
    clock: Clock,
    sleeper: Sleeper,
    health: HealthState,
    stop: Callable[[], bool] = lambda: False,
) -> RunSummary:
    sender = Sender(HubClient(client, feed.adapter_id, token, settings), queue, sleeper)
    source = make_source(feed, settings, clock, queue)
    pipeline = Pipeline(feed, source, queue, sender, health, clock, sleeper, settings, feed.origin)
    return pipeline.run(stop)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[str], httpx.Client] | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    clock, sleeper = clock or SystemClock(), sleeper or SystemSleeper()
    try:
        settings = _settings(args.settings)
        feeds = load_feeds(args.feed, profile=args.profile)
        feed = select_feed(feeds, args.kind, args.feed_id)
        token = os.environ.get(TOKEN_ENV, "")
        if not token:
            raise AdapterError("adapter token missing from " + TOKEN_ENV)
        return _serve(args, feed, token, settings, clock, sleeper, client_factory)
    except QueueStorageError as error:
        print(json.dumps({"event": "adapter_exit", "code": error.code}), file=sys.stderr)
        return EXIT_STORAGE
    except TransportFault as error:
        print(json.dumps({"event": "adapter_exit", "code": error.code}), file=sys.stderr)
        return EXIT_TRANSPORT
    except AdapterError as error:
        print(json.dumps({"event": "adapter_exit", "code": error.code}), file=sys.stderr)
        return EXIT_REFUSED


def _serve(
    args: argparse.Namespace,
    feed: Feed,
    token: str,
    settings: Settings,
    clock: Clock,
    sleeper: Sleeper,
    client_factory: Callable[[str], httpx.Client] | None,
) -> int:
    queue = DurableQueue(args.queue_file, settings, feed.adapter_id)
    health = HealthState(feed.adapter_id, clock, settings)
    server = HealthServer(health, args.health_port)
    server.start()
    client = client_factory(args.hub_url) if client_factory else httpx.Client(base_url=args.hub_url)
    try:
        if args.restart:
            queue.rewind(feed.feed_id)
        summary = run_feed(
            feed,
            client=client,
            token=token,
            queue=queue,
            settings=settings,
            clock=clock,
            sleeper=sleeper,
            health=health,
        )
        print(
            json.dumps(
                {
                    "event": "adapter_done",
                    "enqueued": summary.enqueued,
                    "skipped": summary.skipped,
                    "paused": summary.paused,
                    "fault": summary.fault,
                }
            )
        )
        return EXIT_TRANSPORT if summary.fault is not None or summary.paused else EXIT_OK
    finally:
        client.close()
        server.stop()
        queue.close()


def entry() -> None:
    raise SystemExit(main())


__all__ = ["SystemClock", "SystemSleeper", "main", "run_feed"]
