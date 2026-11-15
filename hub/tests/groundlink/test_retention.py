from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.adapters.sqlite_repository import SqliteGroundLinkRepository
from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.kernel.uow import Actor

from .support import Harness, event


@pytest.mark.trace("R48", "E29-01")
def test_history_retention_preserves_environment_override_and_last_gap(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    deps = h.runtime.services.deps
    deps.repository = SqliteGroundLinkRepository(replace(deps.settings, history_retention_rows=100))
    with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_env") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
        )
    h.client.post(
        "/v1/demo/ground-link", json={"status": "down"}, headers={"Idempotency-Key": "command_0001"}
    )
    with h.db.snapshot() as view:
        gap = h.query.last_gap(view)
        link = h.query.current(view)
        assert link is not None and gap is not None
    for batch in range(10):
        with h.db.begin(actor=Actor("engine", "observer"), correlation_id="cor_observe") as uow:
            for i in range(100):
                deps.repository.append(uow, "observed", h.clock.at + timedelta(seconds=batch * 100 + i), link)
    with h.db.snapshot() as view:
        count = view.connection.rows("SELECT count(*) FROM groundlink_status", (), limit=1)[0][0]
        assert isinstance(count, int) and count <= 102
        assert deps.repository.environment(view) == "station_leo"
        assert deps.repository.override(view) is not None
        assert h.query.current(view) == link
        assert h.query.last_gap(view) == gap


@pytest.mark.trace("R48", "E29-01")
def test_last_gap_reads_gap_primary_key_without_status_history_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(tmp_path)
    with h.db.snapshot() as view:
        queries: list[str] = []
        original = view.connection.rows

        def capture(
            connection: StorageSession, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
        ) -> tuple[tuple[SqlValue, ...], ...]:
            queries.append(sql)
            return original(sql, parameters, limit=limit)

        with monkeypatch.context() as patch:
            patch.setattr(type(view.connection), "rows", capture)
            h.query.last_gap(view)
        assert len(queries) == 1
        assert "ORDER BY gap_seq DESC LIMIT 1" in queries[0]
        plan = view.connection.rows("EXPLAIN QUERY PLAN " + queries[0], (), limit=10)
        details = " ".join(str(row[-1]) for row in plan)
        assert "groundlink_gap" in details and "groundlink_status" not in details
        assert "TEMP B-TREE" not in details
        schema = view.connection.rows(
            "SELECT sql FROM sqlite_master WHERE name='groundlink_gap'", (), limit=1
        )[0][0]
        assert isinstance(schema, str) and "gap_seq INTEGER PRIMARY KEY" in schema


@pytest.mark.trace("R48", "E29-01")
def test_gap_retention_keeps_latest_hundred_and_only_closure_is_mutable(tmp_path: Path) -> None:
    from crew_health.kernel.errors import StorageUnavailable

    h = Harness(tmp_path)
    deps = h.runtime.services.deps
    deps.repository = SqliteGroundLinkRepository(replace(deps.settings, history_retention_rows=100))
    with h.db.snapshot() as view:
        up = h.query.current(view)
        assert up is not None
    down = replace(up, status="down", gap_kind="equipment", gap_planned=False)
    for i in range(110):
        with h.db.begin(actor=Actor("engine", "observer"), correlation_id="cor_gap") as uow:
            deps.repository.append(uow, "observed", h.clock.at + timedelta(seconds=i * 3), down)
            deps.repository.append(
                uow, "observed", h.clock.at + timedelta(seconds=i * 3 + 1), replace(down, gap_kind="unknown")
            )
            deps.repository.append(uow, "observed", h.clock.at + timedelta(seconds=i * 3 + 2), up)
    with h.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM groundlink_gap", (), limit=1) == ((100,),)
        gap = h.query.last_gap(view)
        assert gap is not None and gap.gap_kind == "equipment"
        assert gap.started_at == h.clock.at + timedelta(seconds=327)
        assert gap.ended_at == h.clock.at + timedelta(seconds=329)
    for sql in (
        "DELETE FROM groundlink_gap",
        "UPDATE groundlink_gap SET gap_kind='unknown'",
        "UPDATE groundlink_gap SET ended_at=NULL",
    ):
        with pytest.raises(StorageUnavailable):
            with h.db.begin(actor=Actor("engine", "tamper"), correlation_id="cor_tamper") as uow:
                uow.connection.execute(sql)


@pytest.mark.trace("R48", "E29-01")
def test_pruned_demo_replay_returns_retryable_internal_error(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    deps = h.runtime.services.deps
    deps.repository = SqliteGroundLinkRepository(replace(deps.settings, history_retention_rows=100))
    headers = {"Idempotency-Key": "command_0001"}
    assert h.client.post("/v1/demo/ground-link", json={"status": "down"}, headers=headers).status_code == 200
    assert (
        h.client.post(
            "/v1/demo/ground-link", json={"use_schedule": True}, headers={"Idempotency-Key": "command_0002"}
        ).status_code
        == 200
    )
    with h.db.snapshot() as view:
        link = h.query.current(view)
        assert link is not None
    with h.db.begin(actor=Actor("engine", "observer"), correlation_id="cor_observe") as uow:
        for i in range(101):
            deps.repository.append(uow, "observed", h.clock.at + timedelta(seconds=i), link)
    replay = h.client.post("/v1/demo/ground-link", json={"status": "down"}, headers=headers)
    assert replay.status_code == 500
    assert replay.json()["code"] == "INTERNAL_ERROR" and replay.json()["retryable"] is True
