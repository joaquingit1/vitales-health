"""Shared helpers of the habitat adapter tests. Inputs come from the demo feed files and the NASA package files;
clinical content is never typed into a test. Time, sleeping and the hub are injected fakes."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from crew_health_adapter_habitat.feeds import Feed, load_feeds
from crew_health_adapter_habitat.health import HealthState
from crew_health_adapter_habitat.http_client import HubClient, Sender
from crew_health_adapter_habitat.ports import ClockState, Settings
from crew_health_adapter_habitat.queue import DurableQueue

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "eval" / "scenarios" / "e23" / "demo"
NASA = ROOT / "docs" / "product" / "nasa-data"
SIM_CONFIG = ROOT / "simulators" / "habitat" / "config"
START = datetime(2026, 11, 14, 9, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, at: datetime = START, state: ClockState = "synchronized") -> None:
        self.at, self.sync = at, state

    def now(self) -> datetime:
        return self.at

    def state(self) -> ClockState:
        return self.sync


class FakeSleeper:
    """Advances the fake clock instead of waiting, and records every wait."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock, self.waits = clock, []  # type: ignore[var-annotated]

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.at += timedelta(seconds=seconds)


@dataclass
class FakeHub:
    """A scriptable hub behind httpx.MockTransport. By default it accepts every row of every batch and replays
    an identical (key, body) with the same result, like the real command ledger."""

    requests: list[httpx.Request] = field(default_factory=list)
    script: list[Callable[[httpx.Request], httpx.Response | Exception]] = field(default_factory=list)
    seen: dict[str, tuple[bytes, dict[str, Any]]] = field(default_factory=dict)
    timeouts: list[Any] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.timeouts.append(request.extensions.get("timeout"))
        if self.script:
            step = self.script.pop(0)(request)
            if isinstance(step, Exception):
                raise step
            return step
        return self.accept(request)

    def accept(self, request: httpx.Request, *, record: bool = True) -> httpx.Response:
        key = request.headers["Idempotency-Key"]
        body = request.content
        if key in self.seen:
            prior_body, result = self.seen[key]
            if prior_body != body:
                return httpx.Response(409, json=envelope("DUPLICATE_COMMAND", None))
            return httpx.Response(200, json=result)
        rows = json.loads(body)["readings"]
        result = {
            "batch_id": json.loads(body)["batch_id"],
            "accepted": len(rows),
            "duplicates": 0,
            "reading_ids": [f"rd_{key}_{i}" for i in range(len(rows))],
            "rejected": [],
            "evaluations_changed": [],
        }
        if record:
            self.seen[key] = (body, result)
        return httpx.Response(200, json=result)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://hub.test", transport=httpx.MockTransport(self.handle))


def envelope(code: str, state: dict[str, Any] | None, retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "message": code,
        "retryable": retryable,
        "field_errors": [],
        "correlation_id": "corr_1",
        "current_state": state,
        "supported_alternative": None,
    }


@dataclass
class Rig:
    clock: FakeClock
    sleeper: FakeSleeper
    settings: Settings
    queue: DurableQueue
    hub: FakeHub
    health: HealthState
    tmp: Path

    def sender(self, adapter_id: str = "adp_replay_osdr") -> Sender:
        return Sender(
            HubClient(self.hub.client(), adapter_id, "tok", self.settings), self.queue, self.sleeper
        )

    def reopen(self) -> None:
        """A process restart: the queue is reopened from its file, everything in memory is gone."""
        self.queue.close()
        self.queue = DurableQueue(self.tmp / "queue.db", self.settings, "adp_replay_osdr")


def make_rig(tmp: Path, **overrides: int) -> Rig:
    clock = FakeClock()
    settings = Settings(**overrides) if overrides else Settings()
    queue = DurableQueue(tmp / "queue.db", settings, "adp_replay_osdr")
    return Rig(
        clock,
        FakeSleeper(clock),
        settings,
        queue,
        FakeHub(),
        HealthState("adp_replay_osdr", clock, settings),
        tmp,
    )


def demo_feeds() -> tuple[Feed, ...]:
    return load_feeds(DEMO / "feeds.json", profile="demo")


def feed_by_id(feed_id: str) -> Feed:
    return next(f for f in demo_feeds() if f.feed_id == feed_id)


def scratch_feeds(tmp: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    """A scratch copy of the demo feed directory with an edited feeds.json: the configuration-edit invariant."""
    target = tmp / "scratch"
    shutil.copytree(DEMO, target)
    document = json.loads((target / "feeds.json").read_text(encoding="utf-8"))
    edit(document)
    (target / "feeds.json").write_text(json.dumps(document), encoding="utf-8")
    return target / "feeds.json"
