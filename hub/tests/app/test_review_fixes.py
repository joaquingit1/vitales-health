"""Regression tests for the independent review of fix/e01-composition (findings N1 to N16). Each was a probe that
passed while its defect existed; here it asserts the fixed behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.app.maintenance import FAILED_JOBS, _dispatch
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.platform.adapters.migrations import require_expansion
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.application.supervisor import Supervisor, SupervisorStep
from crew_health.platform.testing.sqlite import database
from crew_health.records.domain.observations import ObservationPolicy, assess_quality

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.5.12")]
ENGINE = Actor("engine", "engine_1")


@pytest.mark.trace("SYSTEM.5.6")
def test_n3_a_raising_head_job_fails_alone_and_later_due_jobs_run(tmp_path: Path) -> None:
    db = database(tmp_path)
    ran: list[str] = []

    def poison(uow: UnitOfWork, job: ScheduledJob) -> None:
        raise ValueError("handler bug")

    db.scheduler.register("poison", poison)
    db.scheduler.register("healthy", lambda uow, job: ran.append(job.key))
    base = datetime(2026, 11, 13, tzinfo=UTC)
    with db.begin(actor=ENGINE, correlation_id="correlation_1") as uow:
        uow.schedule(
            ScheduledJob("job_a", "poison", AbsoluteDeadline(base), {}, None), expected_generation=None
        )
        uow.schedule(
            ScheduledJob("job_b", "healthy", AbsoluteDeadline(base + timedelta(hours=1)), {}, None),
            expected_generation=None,
        )
    health = ComponentHealthState(maximum=64)
    loop = Supervisor(
        steps=(SupervisorStep("scheduler.dispatch", _dispatch(db, health)),),
        settings=db.settings,
        health=health,
        now=db.clock.now,
    )
    assert loop.run_once() == {"scheduler.dispatch": 1}
    assert ran == ["job_b"] and db.scheduler.failed == ("job_a",)
    assert FAILED_JOBS in {c.component for c in health.snapshot().components if c.code is not None}
    assert loop.run_once() == {"scheduler.dispatch": 0}  # the failing job stays due and fails alone again
    assert ran == ["job_b"]


@pytest.mark.trace("R06")
def test_n9_a_unit_mismatch_rejects_and_keeps_the_saturation_reason() -> None:
    policy = ObservationPolicy(
        code="pulse",
        version="1",
        source_ref="probe",
        canonical_unit=Unit("/min"),
        accepted_wire_units=frozenset({"bpm"}),
        methods=frozenset({"device"}),
        minimum=Decimal(0),
        maximum=Decimal(300),
        saturation_minimum=Decimal(20),
        saturation_maximum=Decimal(250),
        stuck_count=3,
        max_change_per_second=Decimal(10),
        manual_checks=True,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    history = ((now - timedelta(seconds=5), Quantity(Decimal(1), Unit("Hz"))),)
    mixed = assess_quality(policy, Quantity(Decimal(250), Unit("/min")), now, history)
    assert mixed.status == "rejected" and "saturation" in mixed.reasons


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TRIGGER help_mute_audit BEFORE INSERT ON platform_audit BEGIN SELECT RAISE(IGNORE); END;",
        "CREATE TRIGGER help_copy AFTER INSERT ON records_observation BEGIN INSERT INTO help_shadow SELECT NEW.payload; END;",
        "CREATE TRIGGER help_guard BEFORE INSERT ON records_observation BEGIN SELECT RAISE(ABORT,'no'); END;",
    ],
)
def test_n11_expand_only_refuses_non_guard_triggers_and_triggers_on_other_modules(sql: str) -> None:
    with pytest.raises(ProblemError):
        require_expansion(sql, module="help")
    require_expansion(
        "CREATE TRIGGER help_guard BEFORE DELETE ON help_session BEGIN SELECT RAISE(ABORT,'no'); END;",
        module="help",
    )
