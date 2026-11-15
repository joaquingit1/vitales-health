"""Review item 1: every completion of a watch step is kept as append-only history (design 4.3 table
plans_run_recheck_entry), so a recheck never overwrites the accepted record of an earlier one."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from crew_health.kernel.errors import ProblemError

from .plans_stack import stack

WATCH = "icl10_pulse_at_cameras"


def stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.mark.trace("T28-12", "E28-04", "R50")
def test_second_completion_appends_and_keeps_the_first(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    first = s.act(run, "step_done", step_id=WATCH, key="first")
    first_at = s.clock.at
    s.clock.at = first_at + timedelta(minutes=30)
    s.act(first, "step_done", step_id=WATCH, key="second")
    with s.db.snapshot() as view:
        entries = view.connection.rows(
            "SELECT occurrence, at, by_kind, by_id FROM plans_run_recheck_entry "
            "WHERE plan_run_id=? AND step_id=? ORDER BY occurrence",
            (str(run["plan_run_id"]), WATCH),
            limit=10,
        )
    assert [tuple(e) for e in entries] == [
        (1, stamp(first_at), "person", "mara"),
        (2, stamp(first_at + timedelta(minutes=30)), "person", "mara"),
    ]


@pytest.mark.trace("T28-12", "E28-04")
def test_recheck_history_rejects_update_and_delete(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.act(run, "step_done", step_id=WATCH, key="first")
    for statement in (
        "UPDATE plans_run_recheck_entry SET by_id='tomas'",
        "DELETE FROM plans_run_recheck_entry",
    ):
        with (
            pytest.raises(ProblemError),
            s.db.begin(actor=s.session.actor, correlation_id="cor_rewrite") as uow,
        ):
            uow.connection.execute(statement)
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT by_id FROM plans_run_recheck_entry", (), limit=10) == (("mara",),)
