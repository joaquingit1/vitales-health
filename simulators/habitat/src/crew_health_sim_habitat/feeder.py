"""Posts readings to the hub through ingestReadings (POST /v1/adapters/{adapter_id}/readings).

This is an interim of E23's own `sim` and `replay` habitat adapters (E23 design 5.2), built under AGENTS
rule 9 until E23 ships them; it is not a device twin. The device boundary is the Bluetooth Environmental
Sensing twin in ble_ess.py, which E23's `ble_ess` adapter parses.

Bounded queue, one batch in flight, retry with capped exponential backoff and jitter while the hub
is down, and a hard outage bound after which the feeder stops loudly. A retried batch keeps its
batch_id, body and Idempotency-Key, so a retry the hub already stored replays as the same command.
The feeder never creates a reading: when the queue is full, offer() refuses and the caller stops
sampling, so an outage longer than the queue shows as a gap.
"""

import json
import os
import random
import urllib.error
import urllib.request
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from crew_health_sim_habitat.config import load_json
from crew_health_sim_habitat.errors import ConfigError, HubRejectedError, HubUnavailableError, SimulatorError
from crew_health_sim_habitat.payload import WireReading, batch_json

CONTRACT_BATCH_MAX = 1000  # requests.schema.json AdapterReadingBatch readings maxItems


@dataclass(frozen=True, slots=True)
class FeederSettings:
    """Operational bounds of the feeder process (settings, not content); each is range checked."""

    queue_max_readings: int = 20000
    batch_max_readings: int = 200
    request_timeout_s: float = 5.0
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0
    outage_max_s: float = 3600.0
    pressure_max_age_s: float = 10.0
    idle_poll_s: float = 0.2
    wait_polls_max: int = 18000

    def __post_init__(self) -> None:
        checks = {
            "queue_max_readings": 1 <= self.queue_max_readings <= 1_000_000,
            "batch_max_readings": 1 <= self.batch_max_readings <= CONTRACT_BATCH_MAX,
            "request_timeout_s": 0.1 <= self.request_timeout_s <= 120.0,
            "backoff_initial_s": 0.01 <= self.backoff_initial_s <= self.backoff_max_s,
            "backoff_max_s": self.backoff_max_s <= 3600.0,
            "outage_max_s": 1.0 <= self.outage_max_s <= 7 * 86400.0,
            "pressure_max_age_s": 0.0 < self.pressure_max_age_s <= 3600.0,
            "idle_poll_s": 0.01 <= self.idle_poll_s <= 10.0,
            "wait_polls_max": 1 <= self.wait_polls_max <= 1_000_000,
        }
        failed = sorted(name for name, ok in checks.items() if not ok)
        if failed:
            raise ConfigError(f"feeder settings out of range: {failed}")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class TransportError(SimulatorError):
    """No HTTP response: connection refused, reset or timed out."""


class Transport(Protocol):
    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse: ...


class UrllibTransport:
    """Standard library HTTP client, so the feeder installs anywhere with no wheels."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return HttpResponse(int(response.status), response.read(1_048_576))
        except urllib.error.HTTPError as exc:
            try:
                return HttpResponse(exc.code, exc.read(1_048_576))
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise TransportError(type(exc).__name__) from exc


class SequenceStore:
    """Per-channel adapter_sequence and the batch counter, persisted before any batch is sent.

    With a path, state is written atomically (temporary file, then os.replace), so a restart never
    reuses a sequence the hub may have stored with a different body.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._channels: dict[str, int] = {}
        self._batch = 0
        if path is not None and path.exists():
            data = load_json(path)
            channels, batch = data.get("channels"), data.get("batch")
            if not isinstance(channels, dict) or not isinstance(batch, int):
                raise ConfigError(f"sequence state {path} is malformed")
            self._channels = {str(k): int(v) for k, v in cast(dict[str, int], channels).items()}
            self._batch = batch

    def next_sequence(self, channel_id: str) -> int:
        value = self._channels.get(channel_id, 0)
        self._channels[channel_id] = value + 1
        return value

    def next_batch(self) -> int:
        self._batch += 1
        return self._batch

    def commit(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {"channels": dict(sorted(self._channels.items())), "batch": self._batch}
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._path)


@dataclass(slots=True)
class FeederStats:
    offered: int = 0
    refused_queue_full: int = 0
    batches_posted: int = 0
    accepted: int = 0
    duplicates: int = 0
    attempts_failed: int = 0
    rejected: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True, slots=True)
class _Batch:
    batch_id: str
    body: bytes
    size: int


class Feeder:
    """One adapter's queue toward the hub. Call offer() for each reading and pump() often."""

    def __init__(
        self,
        *,
        hub_url: str,
        adapter_id: str,
        token: str,
        settings: FeederSettings,
        transport: Transport,
        now: Callable[[], float],
        rng: random.Random,
        sequences: SequenceStore,
        config_release: str | None = None,
    ) -> None:
        if not token:
            raise ConfigError("the adapter token is empty")
        self._url = f"{hub_url.rstrip('/')}/v1/adapters/{adapter_id}/readings"
        self._adapter_id = adapter_id
        self._token = token
        self.settings = settings
        self._transport = transport
        self._now = now
        self._rng = rng
        self._sequences = sequences
        self._config_release = config_release
        self._queue: deque[tuple[int, WireReading]] = deque()
        self._inflight: _Batch | None = None
        self._failures = 0
        self._retry_at = 0.0
        self._outage_since: float | None = None
        self.stats = FeederStats()

    @property
    def queue_full(self) -> bool:
        return len(self._queue) >= self.settings.queue_max_readings

    @property
    def free_slots(self) -> int:
        return self.settings.queue_max_readings - len(self._queue)

    @property
    def pending(self) -> int:
        return len(self._queue) + (self._inflight.size if self._inflight else 0)

    def offer(self, reading: WireReading) -> bool:
        """Queue a reading. False when the queue is full: nothing is queued or invented."""
        if self.queue_full:
            self.stats.refused_queue_full += 1
            return False
        self._queue.append((self._sequences.next_sequence(reading.channel_id), reading))
        self.stats.offered += 1
        return True

    def _form_batch(self) -> _Batch:
        count = min(self.settings.batch_max_readings, len(self._queue))
        items = [self._queue.popleft() for _ in range(count)]
        batch_id = f"bat_{self._adapter_id}_{self._sequences.next_batch():09d}"
        self._sequences.commit()
        body = json.dumps(batch_json(batch_id, items), ensure_ascii=False, separators=(",", ":"))
        return _Batch(batch_id, body.encode("utf-8"), count)

    def _headers(self, batch: _Batch) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Idempotency-Key": batch.batch_id,
            "X-Correlation-Id": batch.batch_id,
        }
        if self._config_release is not None:
            headers["Config-Release"] = self._config_release
        return headers

    def backoff_s(self, failures: int) -> float:
        """Capped exponential backoff with equal jitter: in [cap / 2, cap]."""
        exponent = min(failures - 1, 30)
        cap = min(self.settings.backoff_max_s, self.settings.backoff_initial_s * 2.0**exponent)
        return cap / 2.0 + self._rng.uniform(0.0, cap / 2.0)

    def pump(self) -> bool:
        """Send at most one batch. True when a batch was delivered."""
        now = self._now()
        if self._inflight is None:
            if not self._queue:
                return False
            self._inflight = self._form_batch()
        if now < self._retry_at:
            return False
        batch = self._inflight
        try:
            response = self._transport.post(
                self._url, batch.body, self._headers(batch), self.settings.request_timeout_s
            )
        except TransportError:
            self._failed(now)
            return False
        if response.status >= 500 or response.status == 429:
            self._failed(now)
            return False
        if response.status != 200:
            raise HubRejectedError(response.status, _error_code(response.body), batch.batch_id)
        self._delivered(response.body)
        return True

    def _failed(self, now: float) -> None:
        self.stats.attempts_failed += 1
        self._failures += 1
        if self._outage_since is None:
            self._outage_since = now
        if now - self._outage_since > self.settings.outage_max_s:
            raise HubUnavailableError(
                f"hub unreachable for {now - self._outage_since:.0f} s; {self.pending} readings queued"
            )
        self._retry_at = now + self.backoff_s(self._failures)

    def _delivered(self, body: bytes) -> None:
        result = _parse_result(body)
        self.stats.batches_posted += 1
        self.stats.accepted += _int(result, "accepted")
        self.stats.duplicates += _int(result, "duplicates")
        rejected = result.get("rejected")
        if isinstance(rejected, list):
            for item in rejected:
                code = item.get("code") if isinstance(item, dict) else None
                self.stats.rejected[str(code)] += 1
        self._inflight = None
        self._failures = 0
        self._retry_at = 0.0
        self._outage_since = None


def _parse_result(body: bytes) -> dict[str, object]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HubRejectedError(200, None, "the IngestResult body is not JSON") from exc
    if not isinstance(data, dict):
        raise HubRejectedError(200, None, "the IngestResult body is not an object")
    return cast(dict[str, object], data)


def _int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HubRejectedError(200, None, f"IngestResult.{key} is not a count")
    return value


def _error_code(body: bytes) -> str | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, str) else None


def wait_for_room(feeder: Feeder, sleep: Callable[[float], None], on_delivered: Callable[[], None]) -> None:
    """Pump until the queue has a free slot, at most wait_polls_max polls; then fail loudly."""
    for _ in range(feeder.settings.wait_polls_max):
        if feeder.free_slots > 0:
            return
        if feeder.pump():
            on_delivered()
        else:
            sleep(feeder.settings.idle_poll_s)
    raise HubUnavailableError(f"the queue stayed full for {feeder.settings.wait_polls_max} polls")


def drain(feeder: Feeder, sleep: Callable[[float], None], on_delivered: Callable[[], None]) -> None:
    """Pump until nothing is pending, at most wait_polls_max polls; then fail loudly."""
    for _ in range(feeder.settings.wait_polls_max):
        if feeder.pending == 0:
            return
        if feeder.pump():
            on_delivered()
        else:
            sleep(feeder.settings.idle_poll_s)
    raise HubUnavailableError(f"the queue did not drain within {feeder.settings.wait_polls_max} polls")
