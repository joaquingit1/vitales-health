"""Strict native loopback process entry for the blood pressure adapter."""

import argparse
import threading
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Never

import httpx
import uvicorn

from crew_health_adapter_cuff.family import (
    MEASUREMENT,
    MEASUREMENT_UUID,
    build,
)
from crew_health_device.adapters.ble_source import BleakFrameSource
from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.adapters.http_hub import HttpHubAttempts
from crew_health_device.adapters.profile_file import load_document, load_profile_file
from crew_health_device.adapters.recorded_source import RecordedFrameSource, load_recording_file
from crew_health_device.adapters.system_time import SystemTimeSource
from crew_health_device.adapters.twin_source import twin_source
from crew_health_device.application.delivery import deliver
from crew_health_device.application.ports import FrameSource
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.application.settings import AdapterSettings, load_settings
from crew_health_device.domain.acquisition import OriginMode

# Seconds between twin notifications in a running adapter; tests drive the twin faster.
TWIN_INTERVAL_S = Decimal(1)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise SystemExit("INVALID_ARGUMENTS")


def _source(source: str, settings: AdapterSettings) -> tuple[Callable[[], FrameSource], OriginMode]:
    if source.startswith("ble:"):
        address = source.removeprefix("ble:")
        if not address:
            raise ValueError("invalid_source")

        def live() -> FrameSource:
            return BleakFrameSource(
                address,
                {MEASUREMENT: MEASUREMENT_UUID},
                max_queue=settings.max_frames,
            )

        return live, "LIVE"
    if source.startswith("recording:"):
        document = load_recording_file(Path(source.removeprefix("recording:")), settings.profile_max_bytes)
        recorded = RecordedFrameSource(document, max_frames=settings.max_frames)

        def replay() -> FrameSource:
            return RecordedFrameSource(document, max_frames=settings.max_frames)

        return replay, recorded.origin
    if source.startswith("sim:"):
        # AGENTS.md section 4: the simulated twin behind the same BLE transport; its identity makes it SIM.
        twin = twin_source(
            "cuff",
            source.removeprefix("sim:"),
            {MEASUREMENT: MEASUREMENT_UUID},
            max_queue=settings.max_frames,
            start=SystemTimeSource().now,
            interval_s=TWIN_INTERVAL_S,
        )
        return twin, "SIM"
    raise ValueError("invalid_source")


def _hub_url(value: str) -> str:
    parsed = httpx.URL(value)
    if parsed.scheme not in ("http", "https") or not parsed.host or not parsed.is_absolute_url:
        raise ValueError("invalid_hub_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_hub_url")
    return value.rstrip("/")


def _run(args: argparse.Namespace) -> None:
    # Bootstrap read limit is the specification maximum document size.
    settings = load_settings(load_document(Path(args.settings), 1048576))
    store = ProfileStore(lambda: load_profile_file(Path(args.profile), settings.profile_max_bytes))
    if store.current().family != "cuff" or not 1 <= args.port <= 65535:
        raise ValueError("invalid_configuration")
    token_path = Path(args.token_file)
    with token_path.open("r", encoding="utf-8") as stream:
        token = stream.read(settings.profile_max_bytes + 1).strip()
    if not token or len(token) > settings.profile_max_bytes or any(c.isspace() for c in token):
        raise ValueError("invalid_token")
    hub_url = _hub_url(args.hub_url)
    factory, origin = _source(args.source, settings)
    journal = FileJournal(
        Path(args.journal), max_entries=settings.journal_max_entries, max_bytes=settings.journal_max_bytes
    )
    with httpx.Client() as client:
        hub = HttpHubAttempts(client, base_url=hub_url, token=token, timeout_ms=settings.send_timeout_ms)
        app = build(
            profile_store=store,
            source_factory=factory,
            journal=journal,
            clock=SystemTimeSource(),
            settings=settings,
            adapter_id=args.adapter_id,
            token=token,
            hub=hub,
            source_origin=origin,
            simulated_device=args.source.startswith("sim:"),
            wait=threading.Event().wait,
        )
        deliver(journal, hub, settings)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


def main() -> None:
    parser = _Parser()
    for option in ("settings", "profile", "token-file", "hub-url", "adapter-id", "source", "journal"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    try:
        _run(args)
    except Exception:
        raise SystemExit("ADAPTER_START_OR_RUNTIME_FAILED") from None


if __name__ == "__main__":
    main()
