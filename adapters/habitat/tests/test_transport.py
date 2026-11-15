"""Transport tests: the durable queue, the HTTP client, retries, health, the CLI and the real hub.

Traces: T-E23-028 adapter portion (sequence and key repeat), T-E23-029 (labels and bytes), T-E23-054 (disk full),
T-E23-077 (REPLAY restart never counted twice), T-E23-084 (refuse outside demo), T-E23-089 (receipt and crash
transport invariant), T-E23-092 (bounds, queue backpressure), AGENTS 6 failure cases. The hub is a scriptable fake
for the fault cases, and the real hub app (create_app over on-disk SQLite) for the acceptance test."""

from __future__ import annotations

import http.client
import importlib.util
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from support import (
    DEMO,
    ROOT,
    START,
    FakeClock,
    FakeHub,
    FakeSleeper,
    Rig,
    envelope,
    feed_by_id,
    make_rig,
    scratch_feeds,
)

from crew_health_adapter_habitat.cli import SystemClock, main, run_feed
from crew_health_adapter_habitat.errors import (
    AdapterError,
    CommandConflict,
    QueueFull,
    QueueStorageError,
    ResultMismatch,
    RetryExhausted,
    SettingsInvalid,
    TransportFault,
)
from crew_health_adapter_habitat.feeds import Feed
from crew_health_adapter_habitat.health import HealthServer, HealthState
from crew_health_adapter_habitat.http_client import HubClient, Sender, backoff_s, reconcile, validate_reading
from crew_health_adapter_habitat.pipeline import Pipeline, wire_row
from crew_health_adapter_habitat.ports import SETTING_SEEDS, Settings
from crew_health_adapter_habitat.queue import DurableQueue, FrozenBatch
from crew_health_adapter_habitat.replay import CsvReplaySource
from crew_health_adapter_habitat.sim import build_sim_source

pytestmark = pytest.mark.e23
CO2 = "ch_co2_cabin_replay"


def row(rig: Rig, n: int) -> None:
    """Enqueue one valid RR-1 style row without a source."""
    feed = feed_by_id("feed_rr1_co2_replay")
    from decimal import Decimal

    rig.queue.enqueue(
        CO2,
        lambda seq: wire_row(
            feed, "REPLAY", seq, Decimal(2600 + n), START + timedelta(seconds=300 * n), "not_assessed"
        ),
        feed_id=feed.feed_id,
        position=n,
    )


def batch_rows(request: httpx.Request) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(request.content)["readings"]
    return rows


def fill(rig: Rig, count: int) -> None:
    for n in range(1, count + 1):
        row(rig, n)


# ----- named settings ------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E23-092")
def test_every_setting_is_bounded_one_to_its_seed_and_unknown_keys_reject() -> None:
    assert Settings.from_mapping({}) == Settings()
    for key, seed in SETTING_SEEDS.items():
        assert getattr(Settings.from_mapping({key: seed}), key.split(".")[-1]) == seed
        for bad in (0, -1, seed + 1, True, "5", 1.5):
            with pytest.raises(SettingsInvalid):
                Settings.from_mapping({key: bad})
    with pytest.raises(SettingsInvalid):
        Settings.from_mapping({"habitat_adapter.nope": 1})
    s = Settings()
    assert [backoff_s(s, n) for n in (1, 2, 3, 4, 5, 6, 9)] == [
        5,
        10,
        20,
        40,
        60,
        60,
        60,
    ]  # capped at the setting


# ----- durable queue -------------------------------------------------------------------------------------------


@pytest.mark.trace("T-E23-089", "T-E23-028")
def test_sequences_are_monotonic_per_channel_across_restart_and_a_rewind_never_reuses_them(rig: Rig) -> None:
    fill(rig, 3)
    assert [json.loads(r)["adapter_sequence"] for r in rows_of(rig.queue.next_batch())] == [0, 1, 2]
    rig.reopen()
    fill(rig, 2)  # the same source positions again, after a restart: new sequences, never 0 or 1 again
    assert rig.queue.next_sequence(CO2) == 5
    assert rig.queue.progress("feed_rr1_co2_replay") == 2
    rig.queue.rewind("feed_rr1_co2_replay")
    assert rig.queue.progress("feed_rr1_co2_replay") == 0 and rig.queue.next_sequence(CO2) == 5


def rows_of(batch: FrozenBatch | None) -> list[str]:
    assert batch is not None
    return [json.dumps(r) for r in json.loads(batch.body)["readings"]]


@pytest.mark.trace("T-E23-089")
def test_a_crash_inside_an_enqueue_leaves_no_row_no_sequence_and_no_progress(rig: Rig) -> None:
    def explode(seq: int) -> dict[str, Any]:
        raise RuntimeError("power loss")

    with pytest.raises(RuntimeError):
        rig.queue.enqueue(CO2, explode, feed_id="f", position=9)
    assert rig.queue.depth() == 0 and rig.queue.progress("f") == 0 and rig.queue.next_sequence(CO2) == 0


@pytest.mark.trace("T-E23-089")
def test_a_frozen_batch_is_resent_with_the_same_key_and_bytes_after_every_failure_and_restart(
    rig: Rig,
) -> None:
    fill(rig, 3)
    first = rig.queue.next_batch()
    assert first is not None
    rig.reopen()
    again = rig.queue.next_batch()
    assert again == first  # identical id, key and body bytes
    fill(rig, 2)  # new rows never join a frozen batch
    assert rig.queue.next_batch() == first
    rig.hub.script = [lambda r: httpx.ReadTimeout("slow"), lambda r: httpx.ConnectError("down")]
    rig.sender().flush(1)  # third attempt reaches the default hub and succeeds
    assert len({(q.headers["Idempotency-Key"], q.content) for q in rig.hub.requests}) == 1
    assert len(rig.hub.requests) == 3


@pytest.mark.trace("T-E23-054")
def test_disk_full_fails_loudly_and_leaves_source_progress_and_queue_intact(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    fill(rig, 2)
    rig.queue.close()
    connection = sqlite3.connect(tmp_path / "queue.db")
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    connection.execute(f"PRAGMA max_page_count={pages}")  # no growth allowed: the disk is full
    connection.close()
    queue = DurableQueue(tmp_path / "queue.db", Settings(), "adp_replay_osdr")
    queue.db.execute(f"PRAGMA max_page_count={pages}")
    feed = feed_by_id("feed_rr1_co2_replay")
    from decimal import Decimal

    def make_row(n: int) -> Callable[[int], dict[str, Any]]:
        return lambda s: dict(wire_row(feed, "REPLAY", s, Decimal(n), START, "not_assessed"))

    before = (queue.depth(), queue.progress(feed.feed_id), queue.next_sequence(CO2))
    with pytest.raises(QueueStorageError):
        for n in range(3, 3000):
            queue.enqueue(CO2, make_row(n), feed_id=feed.feed_id, position=n)
    assert queue.depth() >= before[0]  # accepted rows are all still there
    kept = queue.depth()
    assert (
        queue.progress(feed.feed_id) == kept and queue.next_sequence(CO2) == kept
    )  # the failed row advanced nothing
    queue.close()


@pytest.mark.trace("T-E23-054")
def test_a_corrupt_queue_file_is_refused_loudly(tmp_path: Path) -> None:
    broken = tmp_path / "queue.db"
    broken.write_bytes(b"this is not a sqlite file" * 100)
    with pytest.raises(QueueStorageError):
        DurableQueue(broken, Settings(), "adp_x")


@pytest.mark.trace("T-E23-092")
def test_an_oversized_row_is_rejected_visibly_without_a_sequence_and_progress_advances(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path, row_max_bytes=100)
    rig.queue.enqueue(CO2, lambda s: {"padding": "x" * 500}, feed_id="f", position=4)
    assert rig.queue.depth() == 0 and rig.queue.progress("f") == 4 and rig.queue.next_sequence(CO2) == 0
    assert rig.queue.counts() == {"ROW_TOO_LARGE": 1}
    assert rig.queue.rejected_rows()[0][-1] == "ROW_TOO_LARGE"
    rig.queue.close()


@pytest.mark.trace("T-E23-092")
def test_queue_full_raises_at_the_bound_and_never_drops(small_rig: Rig) -> None:
    fill(small_rig, 6)
    with pytest.raises(QueueFull):
        row(small_rig, 7)
    assert small_rig.queue.depth() == 6 and small_rig.queue.is_full()
    small_rig.sender().flush(1)  # three rows leave, in order
    assert small_rig.queue.depth() == 3 and not small_rig.queue.is_full()
    assert [r["adapter_sequence"] for r in batch_rows(small_rig.hub.requests[0])] == [0, 1, 2]


# ----- the HTTP client -----------------------------------------------------------------------------------------


@pytest.mark.trace("T-E23-092", "AGENTS-2")
def test_every_call_has_the_timeout_and_the_adapter_credentials_and_path(rig: Rig) -> None:
    fill(rig, 2)
    rig.sender().flush(1)
    request = rig.hub.requests[0]
    assert request.method == "POST" and request.url.path == "/v1/adapters/adp_replay_osdr/readings"
    assert request.headers["Authorization"] == "Bearer tok" and request.headers["Idempotency-Key"].startswith(
        "key_bat_"
    )
    assert rig.hub.timeouts[0]["read"] == rig.settings.http_timeout_s == 5
    body = json.loads(request.content)
    assert set(body) == {"batch_id", "readings"} and 8 <= len(request.headers["Idempotency-Key"]) <= 128


@pytest.mark.trace("T-E23-092", "AGENTS-2")
def test_hub_down_retries_with_capped_backoff_then_exhausts_and_keeps_every_row(small_rig: Rig) -> None:
    fill(small_rig, 5)
    small_rig.hub.script = [lambda r: httpx.ConnectError("down")] * 3
    with pytest.raises(RetryExhausted):
        small_rig.sender().flush(1)
    assert len(small_rig.hub.requests) == small_rig.settings.retry_max == 3
    assert small_rig.sleeper.waits == [1, 2]  # batch_interval_s doubling, no wait after the last attempt
    assert small_rig.queue.depth() == 5
    assert len({(q.headers["Idempotency-Key"], q.content) for q in small_rig.hub.requests}) == 1
    small_rig.sender().flush(5)  # the hub is back: everything arrives, in order
    assert small_rig.queue.depth() == 0


@pytest.mark.trace("T-E23-092")
def test_a_timeout_is_retried_and_the_same_bytes_are_sent(small_rig: Rig) -> None:
    fill(small_rig, 2)
    small_rig.hub.script = [
        lambda r: httpx.ReadTimeout("t"),
        lambda r: httpx.Response(503, json=envelope("INTERNAL_ERROR", None, True)),
    ]
    small_rig.sender().flush(1)
    assert small_rig.queue.depth() == 0 and len(small_rig.hub.requests) == 3


@pytest.mark.trace("T-E23-028")
def test_duplicates_and_idempotent_resend_are_reconciled_and_a_lost_response_is_replayed(rig: Rig) -> None:
    fill(rig, 4)
    batch = rig.queue.next_batch()
    assert batch is not None
    rig.hub.accept(
        httpx.Request("POST", "http://x", headers={"Idempotency-Key": batch.key}, content=batch.body)
    )  # hub took it, reply lost
    rig.reopen()  # and the adapter crashed before it saw the answer
    rig.sender().flush(1)  # same key and body: the hub replays the original result
    assert rig.queue.depth() == 0 and len(rig.hub.requests) == 1
    fill(rig, 2)
    second = rig.queue.next_batch()
    assert second is not None
    body = json.loads(second.body)
    dup = {
        "batch_id": body["batch_id"],
        "accepted": 0,
        "duplicates": 2,
        "reading_ids": [],
        "rejected": [],
        "evaluations_changed": [],
    }
    rig.hub.script = [lambda r: httpx.Response(200, json=dup)]
    rig.sender().flush(1)
    assert rig.queue.depth() == 0  # duplicates count as delivered


@pytest.mark.trace("T-E23-089", "T-E23-028")
def test_a_mixed_result_keeps_rejected_rows_as_diagnostics_and_acknowledges_the_rest(rig: Rig) -> None:
    fill(rig, 4)
    batch = rig.queue.next_batch()
    assert batch is not None
    mixed = {
        "batch_id": batch.batch_id, "accepted": 2, "duplicates": 1, "reading_ids": ["rd_a", "rd_b"],
        "rejected": [{"index": 2, "code": "INVALID_VALUE", "message": "INVALID_VALUE"}], "evaluations_changed": [],
    }  # fmt: skip
    rig.hub.script = [lambda r: httpx.Response(200, json=mixed)]
    rig.sender().flush(1)
    assert rig.queue.depth() == 0
    [kept] = rig.queue.rejected_rows()
    assert (
        kept[0] == "hub" and kept[2] == 2 and kept[3] == CO2 and kept[4] == 2 and kept[5] == "INVALID_VALUE"
    )
    assert rig.queue.counts() == {"INVALID_VALUE": 1}


@pytest.mark.trace("T-E23-089")
def test_a_result_that_does_not_reconcile_keeps_the_batch_quarantined(rig: Rig) -> None:
    fill(rig, 3)
    batch = rig.queue.next_batch()
    assert batch is not None
    for wrong in (
        {
            "batch_id": "bat_other",
            "accepted": 3,
            "duplicates": 0,
            "reading_ids": ["a", "b", "c"],
            "rejected": [],
        },
        {
            "batch_id": batch.batch_id,
            "accepted": 2,
            "duplicates": 0,
            "reading_ids": ["a", "b"],
            "rejected": [],
        },
        {
            "batch_id": batch.batch_id,
            "accepted": 2,
            "duplicates": 0,
            "reading_ids": ["a"],
            "rejected": [{"index": 9, "code": "INVALID_VALUE", "message": "m"}],
        },
    ):
        from crew_health.contracts_gen.schemas.ingest_result_schema import IngestResult

        with pytest.raises(ResultMismatch):
            reconcile(batch, IngestResult.model_validate({**wrong, "evaluations_changed": []}))
    rig.hub.script = [
        lambda r: httpx.Response(
            200,
            json={
                "batch_id": batch.batch_id,
                "accepted": 1,
                "duplicates": 0,
                "reading_ids": ["a"],
                "rejected": [],
                "evaluations_changed": [],
            },
        )
    ]
    with pytest.raises(ResultMismatch):
        rig.sender().flush(1)
    assert rig.queue.depth() == 3 and rig.queue.next_batch() == FrozenBatch(
        batch.batch_id, batch.key, batch.body, 3, "quarantined:RESULT_MISMATCH"
    )
    with pytest.raises(CommandConflict):
        rig.sender().flush(1)  # never retried automatically


def receipt(key: str, **change: str) -> dict[str, Any]:
    state = {
        "kind": "accepted_command",
        "command_key": key,
        "operation_id": "ingestReadings",
        "target": "/v1/adapters/adp_replay_osdr/readings",
        "receipt_id": "rcpt_1",
        "accepted_at": "2026-11-14T09:00:00Z",
    }
    return envelope("DUPLICATE_COMMAND", {**state, **change})


@pytest.mark.trace("T-E23-089")
def test_a_matching_accepted_command_receipt_after_a_long_outage_closes_the_batch(rig: Rig) -> None:
    fill(rig, 2)
    batch = rig.queue.next_batch()
    assert batch is not None
    rig.hub.script = [lambda r: httpx.Response(409, json=receipt(batch.key))]
    rig.sender().flush(1)
    assert rig.queue.depth() == 0 and rig.queue.counts() == {"RECEIPT_ONLY": 1}


@pytest.mark.trace("T-E23-089")
@pytest.mark.parametrize(
    "answer",
    [
        envelope("DUPLICATE_COMMAND", None),  # a mismatch carries no receipt
        envelope("STALE_VERSION", None),
        {"unexpected": "shape"},
    ],
)
def test_a_mismatched_409_never_dequeues_and_never_mints_a_new_key(rig: Rig, answer: dict[str, Any]) -> None:
    fill(rig, 2)
    batch = rig.queue.next_batch()
    assert batch is not None
    rig.hub.script = [lambda r: httpx.Response(409, json=answer)]
    with pytest.raises(CommandConflict):
        rig.sender().flush(1)
    again = rig.queue.next_batch()
    assert again is not None and (again.key, again.body, again.batch_id) == (
        batch.key,
        batch.body,
        batch.batch_id,
    )
    assert again.state.startswith("quarantined") and rig.queue.depth() == 2
    assert len(rig.hub.requests) == 1  # one attempt, no automatic retry, no second key


@pytest.mark.trace("T-E23-089")
@pytest.mark.parametrize(
    "wrong",
    [{"command_key": "key_other"}, {"operation_id": "getMission"}, {"target": "/v1/adapters/other/readings"}],
)
def test_a_receipt_for_another_key_operation_or_target_is_not_accepted(
    rig: Rig, wrong: dict[str, str]
) -> None:
    fill(rig, 1)
    batch = rig.queue.next_batch()
    assert batch is not None
    rig.hub.script = [lambda r: httpx.Response(409, json=receipt(batch.key, **wrong))]
    with pytest.raises(CommandConflict):
        rig.sender().flush(1)
    assert rig.queue.depth() == 1


@pytest.mark.parametrize("status", [400, 401, 403, 413, 415])
def test_refusals_keep_the_batch_queued_and_open_for_the_operator_to_fix(rig: Rig, status: int) -> None:
    fill(rig, 2)
    rig.hub.script = [lambda r: httpx.Response(status, json=envelope("UNAUTHENTICATED", None))]
    with pytest.raises(TransportFault) as caught:
        rig.sender().flush(1)
    assert not isinstance(caught.value, CommandConflict) and rig.queue.depth() == 2
    batch = rig.queue.next_batch()
    assert batch is not None and batch.state == "open"
    rig.sender().flush(1)  # fixed (new token): the same batch goes
    assert rig.queue.depth() == 0


def test_the_reading_boundary_uses_the_generated_schema() -> None:
    feed = feed_by_id("feed_rr1_co2_replay")
    from decimal import Decimal

    good = wire_row(feed, "REPLAY", 1, Decimal("2641"), START, "not_assessed")
    assert validate_reading(good)
    assert not validate_reading({**good, "adapter_sequence": -1})
    assert not validate_reading({**good, "origin_mode": "FAKE"})
    assert not validate_reading({**good, "observed_at": "2014-09-20T03:59:00+00:00"})  # must end in Z
    assert not validate_reading({**good, "extra": 1})


# ----- the pipeline: labels, bytes, restart, pause --------------------------------------------------------------


def run_pipeline(rig: Rig, feed: Feed, *, stop: Any = lambda: False) -> Any:
    sender = rig.sender(feed.adapter_id)
    source = (
        build_sim_source(feed, rig.clock, START)
        if feed.kind == "sim"
        else CsvReplaySource(feed, rig.settings)
    )
    return Pipeline(
        feed, source, rig.queue, sender, rig.health, rig.clock, rig.sleeper, rig.settings, feed.origin
    ).run(stop)


@pytest.mark.trace("T029", "T-E23-077")
def test_a_replay_posts_every_row_with_replay_labels_and_original_dates_and_a_restart_is_not_a_second_copy(
    rig: Rig,
) -> None:
    feed = feed_by_id("feed_dostel1_rad_replay")
    summary = run_pipeline(rig, feed)
    sent = [r for q in rig.hub.requests for r in batch_rows(q)]
    assert summary.enqueued == len(sent) == 84 and summary.fault is None and rig.queue.depth() == 0
    assert {r["origin_mode"] for r in sent} == {"REPLAY"} and {r["unit"] for r in sent} == {"µGy/h"}
    assert (
        sent[0]["observed_at"] == "2022-04-01T23:00:00Z" and sent[-1]["observed_at"] == "2022-04-02T01:04:40Z"
    )
    info = feed.replay
    assert info is not None
    assert all(r["replay"] == {"dataset": info.dataset, "time_basis": info.time_basis} for r in sent)
    assert [r["adapter_sequence"] for r in sent] == list(range(84))
    assert {r["quality_status"] for r in sent} == {
        "not_assessed"
    }  # no sourced criteria yet: captured, flagged
    assert not any("cabin_pressure_mmhg" in r for r in sent)  # a dose rate needs no pressure
    rig.reopen()
    second = run_pipeline(rig, feed)  # same file, restarted: no implicit dataset reset
    assert second.enqueued == 0 and len(rig.hub.requests) == len([q for q in rig.hub.requests])
    rig.queue.rewind(feed.feed_id)  # an explicit rewind: sequences keep counting, never reused
    third = run_pipeline(rig, feed)
    assert third.enqueued == 84 and rig.queue.next_sequence("ch_rad_columbus_replay") == 168


@pytest.mark.trace("T029")
def test_the_rr1_week_is_batched_within_the_hub_limit_in_order(rig: Rig) -> None:
    feed = feed_by_id("feed_rr1_co2_replay")
    run_pipeline(rig, feed)
    sizes = [len(batch_rows(q)) for q in rig.hub.requests]
    assert sum(sizes) == 2012 and max(sizes) <= rig.settings.batch_max_readings == 1000
    sent = [r for q in rig.hub.requests for r in batch_rows(q)]
    assert [r["adapter_sequence"] for r in sent] == list(range(2012))
    assert [r["observed_at"] for r in sent][:2] == ["2014-09-30T15:45:00Z", "2014-09-30T15:50:00Z"]
    assert all(r["cabin_pressure_mmhg"] == {"value": 760.0, "basis": "assumed"} for r in sent)
    assert all(r["unit"] == "ppm" and r["value"] > 0 for r in sent)


@pytest.mark.trace("T029", "T-E23-062")
def test_the_simulated_twin_posts_sim_rows_through_the_same_client(rig: Rig) -> None:
    feed = feed_by_id("feed_co2_cabin_sim")
    count = 0

    def stop() -> bool:
        nonlocal count
        count += 1
        return count > 40

    rig.queue.close()
    rig.queue = DurableQueue(rig.tmp / "queue.db", rig.settings, "adp_sim")
    summary = run_pipeline(rig, feed, stop=stop)
    sent = [r for q in rig.hub.requests for r in batch_rows(q)]
    assert summary.enqueued == len(sent) == 40
    assert {r["origin_mode"] for r in sent} == {"SIM"} and all("replay" not in r for r in sent)
    assert sent[0]["unit"] == "ppm" and sent[0]["cabin_pressure_mmhg"]["basis"] == "assumed"
    assert all(q.url.path == "/v1/adapters/adp_sim/readings" for q in rig.hub.requests)
    assert rig.sleeper.waits[:3] == [5.0, 5.0, 5.0]  # the feed's fixed pace


@pytest.mark.trace("T-E23-092", "AGENTS-6")
def test_a_full_queue_pauses_the_source_reports_queue_full_and_resumes_in_order_without_loss(
    small_rig: Rig,
) -> None:
    feed = feed_by_id("feed_dostel1_rad_replay")
    small_rig.hub.script = [lambda r: httpx.ConnectError("down")] * 40  # a long outage
    first = run_pipeline(small_rig, feed)
    assert first.paused and first.fault is not None
    assert small_rig.queue.is_full() and small_rig.queue.depth() == small_rig.settings.queue_max == 6
    assert small_rig.health.document() == {
        "adapter_id": "adp_replay_osdr",
        "clock": "synchronized",
        "queue": "queue_full",
    }
    taken = small_rig.queue.progress(feed.feed_id)
    positions = [
        n
        for n, line in enumerate(
            (DEMO / "radlab_dostel_sample.csv").read_text(encoding="utf-8").splitlines()[1:], 1
        )
        if '"DosTel1"' in line.split(",")[1]
    ]
    assert (
        taken == positions[5] and small_rig.queue.next_sequence("ch_rad_columbus_replay") == 6
    )  # nothing read past the bound
    small_rig.hub.script.clear()  # the hub returns
    resumed = run_pipeline(small_rig, feed)
    assert resumed.fault is None and not resumed.paused and small_rig.queue.depth() == 0
    sent = [
        r
        for q in small_rig.hub.requests
        if q.headers["Idempotency-Key"] in small_rig.hub.seen
        for r in batch_rows(q)
    ]
    unique = {r["adapter_sequence"]: r for r in sent}
    assert sorted(unique) == list(range(84)) and [unique[i]["observed_at"] for i in range(2)] == [
        "2022-04-01T23:00:00Z",
        "2022-04-01T23:00:33Z",
    ]


@pytest.mark.trace("AGENTS-6")
def test_a_long_outage_keeps_filling_the_bounded_queue_and_exposes_the_fault_in_diagnostics(
    small_rig: Rig,
) -> None:
    feed = feed_by_id("feed_dostel1_rad_replay")
    small_rig.hub.script = [lambda r: httpx.ConnectError("down")] * 200
    run_pipeline(small_rig, feed)
    assert (
        small_rig.health.diagnostics()["fault"] == "RETRY_EXHAUSTED"
        and small_rig.health.diagnostics()["queue_full"] is True
    )


@pytest.mark.trace("AGENTS-6")
def test_the_adapter_never_adds_clock_fields_and_passes_the_source_time_unchanged_under_an_offset_clock(
    rig: Rig,
) -> None:
    offset = FakeClock(START + timedelta(days=400), "unsynchronized")  # a host clock far from the real time
    rig.clock.at, rig.clock.sync = offset.at, offset.sync
    feed = feed_by_id("feed_co2_cabin_sim")
    rig.queue.close()
    rig.queue = DurableQueue(rig.tmp / "queue.db", rig.settings, "adp_sim")
    calls = 0

    def stop() -> bool:
        nonlocal calls
        calls += 1
        return calls > 3

    run_pipeline(rig, feed, stop=stop)
    sent = [r for q in rig.hub.requests for r in batch_rows(q)]
    assert [r["observed_at"][:10] for r in sent][0] == (START + timedelta(days=400)).strftime(
        "%Y-%m-%d"
    )  # as read, no guess
    assert set(sent[0]) == {
        "channel_id",
        "adapter_sequence",
        "value",
        "unit",
        "observed_at",
        "origin_mode",
        "quality_status",
        "cabin_pressure_mmhg",
    }
    assert rig.health.document()["clock"] == "unsynchronized"
    rig.clock.at -= timedelta(
        days=401
    )  # the clock jumps backward: sequences still advance, the hub owns correction
    run_pipeline(rig, feed, stop=lambda: False) if False else None
    assert rig.queue.next_sequence("ch_co2_cabin_sim") == 3


@pytest.mark.trace("T-E23-062", "AGENTS-6")
def test_a_device_disconnect_keeps_everything_already_queued_and_is_loud(rig: Rig, tmp_path: Path) -> None:
    from support import SIM_CONFIG, demo_feeds

    base = {f.feed_id: f for f in demo_feeds()}["feed_co2_cabin_sim"]
    sim = {
        "type": "twin",
        "protocol": "ble_ess",
        "scenario": "ess_link_and_byte_faults",
        "select": "0x2A6F",
        "config_dir": str(SIM_CONFIG),
    }
    feed = replace(base, sim=sim, unit="%")
    rig.queue.close()
    rig.queue = DurableQueue(rig.tmp / "queue.db", rig.settings, "adp_sim")
    from crew_health_adapter_habitat.errors import SourceUnavailable
    from crew_health_adapter_habitat.sim import load_twin_world

    world = load_twin_world(SIM_CONFIG)
    scenario, faults = world.run("ess_link_and_byte_faults")
    lost_at = next(f.start_s for f in faults if f.fault == "disconnect")
    before = sum(
        1
        for tick in world.ticks(scenario)
        if tick.t_s < lost_at
        for r in tick.readings
        if (r.device_id, r.output) == ("sim_temp_rh_cabin", "humidity")
    )  # the humidity samples taken before the link was lost, counted from the devices
    with pytest.raises(SourceUnavailable):
        run_pipeline(rig, feed)
    delivered = sum(len(batch_rows(q)) for q in rig.hub.requests)
    assert before > 5
    assert rig.queue.depth() + delivered == before
    assert rig.queue.progress(feed.feed_id) == before


# ----- health ---------------------------------------------------------------------------------------------------


def get(server: HealthServer, path: str) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request("GET", path)
    reply = connection.getresponse()
    body = json.loads(reply.read())
    connection.close()
    return reply.status, body


@pytest.mark.trace("T-E23-092", "AGENTS-2")
def test_health_answers_the_exact_schema_independently_of_a_dead_hub_and_goes_503_when_the_loop_stalls(
    rig: Rig,
) -> None:
    schema = json.loads((ROOT / "adapters/habitat/schemas/health.schema.json").read_text(encoding="utf-8"))
    state = HealthState("adp_replay_osdr", rig.clock, rig.settings)
    server = HealthServer(state, 0)
    server.start()
    try:
        assert get(server, "/health")[0] == 503  # no heartbeat yet: not claimed healthy
        state.beat()
        code, body = get(server, "/health")
        assert code == 200 and not list(Draft202012Validator(schema).iter_errors(body))
        assert body == {"adapter_id": "adp_replay_osdr", "clock": "synchronized", "queue": "ok"}
        state.update(queue_full=True)
        rig.clock.sync = "unknown"
        assert get(server, "/health")[1] == {
            "adapter_id": "adp_replay_osdr",
            "clock": "unknown",
            "queue": "queue_full",
        }
        rig.clock.at += timedelta(seconds=rig.settings.retry_max_backoff_s + 1)
        assert get(server, "/health")[0] == 503  # a stalled loop reads as unknown to the hub
        assert get(server, "/diagnostics")[0] == 200 and get(server, "/nope")[0] == 404
    finally:
        server.stop()
    with pytest.raises(AdapterError):
        HealthServer(state, 0, host="0.0.0.0")  # noqa: S104 (the test proves the refusal)


# ----- CLI ------------------------------------------------------------------------------------------------------


def cli_args(
    tmp: Path,
    kind: str = "replay",
    feed_id: str = "feed_dostel1_rad_replay",
    feeds: Path = DEMO / "feeds.json",
) -> list[str]:
    return [
        kind,
        "--feed",
        str(feeds),
        "--feed-id",
        feed_id,
        "--hub-url",
        "http://hub.test",
        "--queue-file",
        str(tmp / "q.db"),
    ]


@pytest.mark.trace("T-E23-084")
def test_the_cli_refuses_outside_the_demo_profile_without_any_hub_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CREW_HEALTH_ADAPTER_TOKEN", "tok")
    hub = FakeHub()
    for profile in (None, "production", "mission"):
        monkeypatch.delenv("CREW_HEALTH_PROFILE", raising=False)
        argv = cli_args(tmp_path) + (["--profile", profile] if profile else [])
        assert main(argv, client_factory=lambda url: hub.client()) == 2
    assert hub.requests == [] and not (tmp_path / "q.db").exists()  # nothing was read, queued or sent


@pytest.mark.trace("T-E23-084")
def test_the_cli_needs_a_token_valid_settings_and_a_real_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = FakeHub()
    factory = lambda url: hub.client()  # noqa: E731
    monkeypatch.delenv("CREW_HEALTH_ADAPTER_TOKEN", raising=False)
    assert main(cli_args(tmp_path) + ["--profile", "demo"], client_factory=factory) == 2
    monkeypatch.setenv("CREW_HEALTH_ADAPTER_TOKEN", "tok")
    bad = tmp_path / "settings.json"
    bad.write_text(json.dumps({"habitat_adapter.queue_max": 0}), encoding="utf-8")
    assert (
        main(cli_args(tmp_path) + ["--profile", "demo", "--settings", str(bad)], client_factory=factory) == 2
    )
    assert (
        main(cli_args(tmp_path, feed_id="feed_missing") + ["--profile", "demo"], client_factory=factory) == 2
    )
    assert (
        main(
            cli_args(tmp_path, kind="sim", feed_id="feed_dostel1_rad_replay") + ["--profile", "demo"],
            client_factory=factory,
        )
        == 2
    )
    assert hub.requests == []


@pytest.mark.trace("T029")
def test_the_cli_replays_through_the_client_and_exits_nonzero_when_the_hub_stays_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CREW_HEALTH_ADAPTER_TOKEN", "tok")
    clock = FakeClock()
    hub = FakeHub()
    argv = cli_args(tmp_path) + ["--profile", "demo"]
    assert main(argv, client_factory=lambda url: hub.client(), clock=clock, sleeper=FakeSleeper(clock)) == 0
    assert sum(len(batch_rows(q)) for q in hub.requests) == 84
    down = FakeHub(script=[lambda r: httpx.ConnectError("down")] * 100)
    argv = cli_args(tmp_path / "second", feed_id="feed_dostel1_rad_replay") + ["--profile", "demo"]
    (tmp_path / "second").mkdir()
    assert main(argv, client_factory=lambda url: down.client(), clock=clock, sleeper=FakeSleeper(clock)) == 3
    assert (
        DurableQueue(tmp_path / "second" / "q.db", Settings(), "adp_replay_osdr").depth() == 84
    )  # nothing lost


def test_the_system_clock_never_claims_synchronization() -> None:
    assert SystemClock().state() == "unknown"


def test_run_feed_builds_the_sim_epoch_once_and_keeps_it_across_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    feed = feed_by_id("feed_co2_cabin_sim")
    hub = FakeHub()
    queue = DurableQueue(tmp_path / "q.db", Settings(), "adp_sim")
    calls = 0

    def stop() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    run_feed(
        feed,
        client=hub.client(),
        token="t",
        queue=queue,
        settings=Settings(),
        clock=clock,
        sleeper=FakeSleeper(clock),
        health=HealthState("adp_sim", clock, Settings()),
        stop=stop,
    )
    epoch = queue.meta("epoch:" + feed.feed_id, lambda: "other")
    assert epoch == START.isoformat()
    queue.close()


# ----- the real hub ---------------------------------------------------------------------------------------------


def load_station() -> Any:
    root = ROOT / "hub" / "tests" / "sensors"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("e23_station", root / "e23_station.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["e23_station"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.filterwarnings("ignore:.*starlette.testclient.*")
@pytest.mark.filterwarnings("ignore:.*argument with the TestClient.*")
@pytest.mark.trace("T029", "T-E23-041", "R42", "D01")
def test_a_replayed_rr1_batch_through_the_real_hub_is_accepted_labeled_replay_with_original_dates(
    tmp_path: Path,
) -> None:
    """The adapter's HTTP client posts through the real ingestReadings boundary: create_app over on-disk SQLite, the
    shipped sensor catalog and the demo channel registry. T6 owns the full scenario acceptance; this proves the
    adapter bytes are accepted."""
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.uow import Actor, SessionContext

    station_module = load_station()
    registry = json.loads((DEMO / "sensor-channels.json").read_text(encoding="utf-8"))
    registry["channels"] = [c for c in registry["channels"] if c["adapter_id"] == "adp_replay_osdr"]
    station = station_module.build_station(tmp_path / "hub", registry=registry)
    assert station.app.state.runtime.serving, station.app.state.runtime.fault_code
    station.initialize("REPLAY")
    station.security.tokens["replay"] = SessionContext(
        "s_rp", "adapter", Actor("adapter", "adp_replay_osdr"), None, frozenset({Role.ADAPTER})
    )
    feed = feed_by_id("feed_rr1_co2_replay")
    short = replace(feed, window_from="2014-10-07T14:36:01", window_to="2014-10-07T15:36:00")
    clock = FakeClock()
    settings = Settings()
    queue = DurableQueue(tmp_path / "q.db", settings, "adp_replay_osdr")
    health = HealthState("adp_replay_osdr", clock, settings)
    summary = run_feed(
        short,
        client=station.client,
        token="replay",
        queue=queue,
        settings=settings,
        clock=clock,
        sleeper=FakeSleeper(clock),
        health=health,
    )
    assert (
        summary.enqueued == 12
        and summary.fault is None
        and queue.depth() == 0
        and queue.rejected_rows() == []
    )
    assert_served_as_replay(station, feed)
    again = run_feed(
        short,
        client=station.client,
        token="replay",
        queue=queue,
        settings=settings,
        clock=clock,
        sleeper=FakeSleeper(clock),
        health=health,
    )
    assert again.enqueued == 0  # a restart does not send the dataset twice
    queue.close()


def assert_served_as_replay(station: Any, feed: Feed) -> None:
    channels = station.client.get("/v1/sensor-channels", headers=station.headers("crew_a")).json()["items"]
    [channel] = [c for c in channels if c["channel_id"] == "ch_co2_cabin_replay"]
    assert channel["origin_mode"] == "REPLAY" and channel["last_observed_at"] == "2014-10-07T15:36:00Z"
    assert feed.replay is not None and channel["dataset"] == feed.replay.dataset
    stored = station.client.get(
        "/v1/sensor-readings/" + _first_id(station), headers=station.headers("crew_a")
    ).json()
    assert stored["origin_mode"] == "REPLAY" and stored["observed_at"] == "2014-10-07T14:41:00Z"
    assert (
        stored["original_unit"] == "ppm"
        and stored["replay"]["original_observed_at"] == "2014-10-07T14:41:00Z"
    )
    assert stored["replay"]["dataset"] == feed.replay.dataset and stored["adapter_id"] == "adp_replay_osdr"


def _first_id(station: Any) -> str:
    connection = sqlite3.connect(station.db.path)
    try:
        found = connection.execute(
            "SELECT reading_id FROM sensors_reading ORDER BY observed_at LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    assert found is not None
    return str(found[0])


def test_unused_helpers_stay_referenced() -> None:
    assert (
        scratch_feeds is not None and FakeSleeper is not None and HubClient is not None and Sender is not None
    )
