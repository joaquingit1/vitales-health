"""The feeder against a stub hub on loopback: contract headers, retries, backoff and no invented backlog."""

import itertools
import json
import random
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.errors import ConfigError, HubRejectedError, HubUnavailableError
from crew_health_sim_habitat.feeder import Feeder, FeederSettings, SequenceStore, UrllibTransport
from crew_health_sim_habitat.payload import WireReading
from crew_health_sim_habitat.runner import WireMapper, build_devices, feed_scenario, simulate
from crew_health_sim_habitat.units import Quantity

from conftest import Validate

ADAPTER = "adp_sim_habitat"
TOKEN = "test-token"
START = datetime(2026, 11, 14, tzinfo=UTC)

type Respond = Callable[[dict[str, object]], tuple[int, dict[str, object]]]


def accept_all(body: dict[str, object]) -> tuple[int, dict[str, object]]:
    readings = body["readings"]
    assert isinstance(readings, list)
    return 200, {
        "batch_id": body["batch_id"],
        "accepted": len(readings),
        "duplicates": 0,
        "reading_ids": [f"rdg_{i}" for i in range(len(readings))],
        "rejected": [],
        "evaluations_changed": [],
    }


@dataclass
class StubHub:
    respond: Respond = accept_all
    requests: list[tuple[str, dict[str, str], dict[str, object]]] = field(default_factory=list)
    url: str = ""

    def accepted_readings(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for _, _, body in self.requests:
            readings = body["readings"]
            assert isinstance(readings, list)
            out.extend(readings)
        return out


@pytest.fixture
def hub() -> Iterator[StubHub]:
    stub = StubHub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            status, payload = stub.respond(body)
            if status == 200:
                stub.requests.append((self.path, dict(self.headers.items()), body))
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield stub
    server.shutdown()
    server.server_close()


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


def make_feeder(
    url: str, clock: FakeClock, settings: FeederSettings | None = None, store: SequenceStore | None = None
) -> Feeder:
    return Feeder(
        hub_url=url,
        adapter_id=ADAPTER,
        token=TOKEN,
        settings=settings or FeederSettings(),
        transport=UrllibTransport(),
        now=clock.now,
        rng=random.Random(7),
        sequences=store or SequenceStore(None),
    )


def reading(channel: str, second: int, value: float = 1.0) -> WireReading:
    return WireReading(channel, Quantity(value, "ppm"), START.replace(second=second), "SIM", "accepted")


def test_posts_the_contract_operation_with_its_headers(hub: StubHub, validate_batch: Validate) -> None:
    feeder = make_feeder(hub.url, FakeClock())
    for second in range(3):
        assert feeder.offer(reading("ch_co2_cabin_sim", second))
    assert feeder.pump()
    path, headers, body = hub.requests[0]
    assert path == f"/v1/adapters/{ADAPTER}/readings"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"
    assert 8 <= len(headers["Idempotency-Key"]) <= 128
    assert headers["Idempotency-Key"] == body["batch_id"]
    validate_batch(body)
    readings = body["readings"]
    assert isinstance(readings, list)
    assert [r["adapter_sequence"] for r in readings] == [0, 1, 2]
    assert feeder.stats.accepted == 3


def test_retries_the_same_batch_with_capped_backoff(hub: StubHub) -> None:
    script = iter([503, 503, 200])
    seen: list[dict[str, object]] = []

    def respond(body: dict[str, object]) -> tuple[int, dict[str, object]]:
        seen.append(body)
        status = next(script)
        return accept_all(body) if status == 200 else (status, {"code": "INTERNAL_ERROR"})

    hub.respond = respond
    clock = FakeClock()
    feeder = make_feeder(hub.url, clock, FeederSettings(backoff_initial_s=1.0, backoff_max_s=60.0))
    feeder.offer(reading("ch_co2_cabin_sim", 0))
    assert not feeder.pump()
    assert not feeder.pump()  # still inside the first backoff window: no request
    assert len(seen) == 1
    for attempt, (low, high) in enumerate([(0.5, 1.0), (1.0, 2.0)], start=2):
        clock.t += high + 1e-6
        delivered = feeder.pump()
        assert len(seen) == attempt
        assert delivered == (attempt == 3)
        assert low <= feeder.backoff_s(attempt - 1) <= high
    assert all(body == seen[0] for body in seen)
    assert feeder.stats.attempts_failed == 2
    assert feeder.pending == 0


def test_backoff_stays_within_its_cap() -> None:
    feeder = make_feeder(
        "http://127.0.0.1:9", FakeClock(), FeederSettings(backoff_initial_s=1.0, backoff_max_s=8.0)
    )
    for failures in range(1, 40):
        cap = min(8.0, 2.0 ** (failures - 1))
        assert cap / 2 <= feeder.backoff_s(failures) <= cap


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_connection_refused_is_retried_then_bounded() -> None:
    clock = FakeClock()
    feeder = make_feeder(
        f"http://127.0.0.1:{_closed_port()}", clock, FeederSettings(outage_max_s=30.0, backoff_max_s=10.0)
    )
    feeder.offer(reading("ch_co2_cabin_sim", 0))
    assert not feeder.pump()
    assert feeder.pending == 1

    def pump_through_the_outage() -> None:
        for _ in range(100):
            clock.t += 10.0
            feeder.pump()

    with pytest.raises(HubUnavailableError):
        pump_through_the_outage()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "UNAUTHENTICATED"),
        (403, "ADAPTER_NOT_TRUSTED"),
        (409, "DUPLICATE_COMMAND"),
        (400, "MALFORMED_REQUEST"),
    ],
)
def test_refusals_stop_loudly_and_are_not_retried(hub: StubHub, status: int, code: str) -> None:
    hub.respond = lambda body: (status, {"code": code})
    feeder = make_feeder(hub.url, FakeClock())
    feeder.offer(reading("ch_co2_cabin_sim", 0))
    with pytest.raises(HubRejectedError) as raised:
        feeder.pump()
    assert raised.value.status == status
    assert raised.value.code == code


def test_sequences_survive_a_restart(tmp_path: Path, hub: StubHub) -> None:
    state = tmp_path / "adp.json"
    feeder = make_feeder(hub.url, FakeClock(), store=SequenceStore(state))
    feeder.offer(reading("ch_co2_cabin_sim", 0))
    feeder.offer(reading("ch_co2_cabin_sim", 1))
    feeder.pump()
    again = make_feeder(hub.url, FakeClock(), store=SequenceStore(state))
    again.offer(reading("ch_co2_cabin_sim", 2))
    again.pump()
    sequences = [r["adapter_sequence"] for r in hub.accepted_readings()]
    batch_ids = [body["batch_id"] for _, _, body in hub.requests]
    assert sequences == [0, 1, 2]
    assert len(set(batch_ids)) == 2


def test_settings_are_bounded() -> None:
    with pytest.raises(ConfigError):
        FeederSettings(batch_max_readings=1001)
    with pytest.raises(ConfigError):
        FeederSettings(queue_max_readings=0)


def test_an_outage_leaves_a_gap_and_nothing_invented(
    loaded: Loaded, hub: StubHub, validate_batch: Validate
) -> None:
    """Hub down from minute 5 to 15 with a 40-reading queue.

    Queued readings arrive late; samples taken while the queue was full are a gap.
    """
    scenario = replace(loaded.scenario("station_nominal"), duration_s=20 * 60.0)
    devices = build_devices(scenario, loaded.wiring, loaded.kinds)
    clock = FakeClock()

    def respond(body: dict[str, object]) -> tuple[int, dict[str, object]]:
        if 300.0 <= clock.t < 900.0:
            return 503, {"code": "INTERNAL_ERROR"}
        return accept_all(body)

    hub.respond = respond
    feeder = make_feeder(
        hub.url,
        clock,
        FeederSettings(
            queue_max_readings=40, batch_max_readings=10, backoff_initial_s=1.0, backoff_max_s=4.0
        ),
    )

    def wait_until(t_s: float) -> None:
        clock.t = t_s

    mapper = WireMapper(loaded.wiring, START, devices, 10.0)
    report = feed_scenario(simulate(scenario, devices), mapper, feeder, wait_until)
    for _ in range(100):
        clock.t += 5.0
        feeder.pump()
    received = hub.accepted_readings()
    validate_batch({"batch_id": "bat_all", "readings": received[:1000]})
    assert feeder.pending == 0
    assert len(received) == report.offered == feeder.stats.accepted
    assert report.skipped_queue_full > 0
    by_channel: dict[str, list[dict[str, object]]] = {}
    for item in received:
        by_channel.setdefault(str(item["channel_id"]), []).append(item)
    co2 = by_channel["ch_co2_cabin_sim"]
    assert [r["adapter_sequence"] for r in co2] == list(range(len(co2)))
    times = [datetime.fromisoformat(str(r["observed_at"]).replace("Z", "+00:00")) for r in co2]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    assert max(gaps) > 60.0  # samples not taken while the queue was full are missing, not filled
    assert all(g % 5 == 0 for g in gaps)  # every reading sits on a real 5 s sample instant
