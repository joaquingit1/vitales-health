from __future__ import annotations

import pytest

from crew_health.assistant.application.runtime_actions import RuntimeActions
from crew_health.assistant.domain.runtime import Runtime, RuntimeState
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.model.application.ports import ModelRelease, RuntimeProbe
from crew_health.platform.storage import SQLiteDatabase

from .test_store import ACTOR, STORE, db

assert db


def release(mode: str = "stub") -> ModelRelease:
    # Control boundary fixture only; real release/hash admission belongs to E17 and its composed tests.
    return ModelRelease(canonical_json({"release_id": "model_A", "mode": mode}))


@pytest.mark.trace("T-E13-075", "T-E13-063", "V69")
def test_boot_has_no_invented_model_mode_and_get_is_readonly(db: SQLiteDatabase) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        value = actions.boot(uow)
        assert (
            value.runtime == Runtime(False, RuntimeState.STOPPED)
            and value.model_release_id is None
            and value.mode is None
        )
    with db.snapshot() as view:
        assert actions.current(view) == value
        assert STORE.runtime_version(view) == 1


@pytest.mark.trace("T-E13-075", "T-E13-076", "V69")
def test_missing_release_is_not_queued_and_flight_stub_remains_unavailable(db: SQLiteDatabase) -> None:
    actions = RuntimeActions(STORE, profile="flight", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        with pytest.raises(ProblemError) as error:
            actions.request(uow, start=True, release=None)
        assert error.value.code == ErrorCode.INVALID_TRANSITION
        refused = actions.request(uow, start=True, release=release())
        assert (
            refused.runtime.state == RuntimeState.UNAVAILABLE
            and refused.error_code == ErrorCode.CONFIG_INVALID
        )
        assert actions.boot(uow).runtime.state == RuntimeState.UNAVAILABLE


@pytest.mark.trace("T-E13-075", "T-E13-024", "V27")
def test_stop_start_aba_fences_old_probe_and_valid_probe_admits_only_matching_generation(
    db: SQLiteDatabase,
) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        loading = actions.request(uow, start=True, release=release())
        old = STORE.runtime_version(uow)
        assert loading.runtime.state == RuntimeState.LOADING
        assert actions.request(uow, start=True, release=None) == loading
        assert STORE.runtime_version(uow) == old
        actions.request(uow, start=False, release=None)
        actions.request(uow, start=True, release=release())
        assert (
            actions.apply_probe(
                uow, expected_version=old, result=RuntimeProbe(True, True, True, "stub"), load_expired=False
            )
            is None
        )
        current = STORE.runtime_version(uow)
        ready = actions.apply_probe(
            uow, expected_version=current, result=RuntimeProbe(True, True, True, "stub"), load_expired=False
        )
        assert ready is not None and ready.runtime.state == RuntimeState.RUNNING


@pytest.mark.trace("T-E13-075", "T-E13-065", "V27")
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeProbe(False, False, False, "model_identity_mismatch"),
        RuntimeProbe(False, False, False, "release_changed"),
        RuntimeProbe(True, True, False, "model_identity_mismatch"),
        RuntimeProbe(False, False, False, "unknown_failure"),
    ],
)
def test_failed_identity_or_unknown_failure_never_looks_like_cold_loading(
    db: SQLiteDatabase, failure: RuntimeProbe
) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        actions.request(uow, start=True, release=release())
        value = actions.apply_probe(
            uow, expected_version=STORE.runtime_version(uow), result=failure, load_expired=False
        )
        assert value is not None and value.runtime.state == RuntimeState.UNAVAILABLE


@pytest.mark.trace("T-E13-075", "T-E13-023", "V27", "SYSTEM.5.2")
def test_cold_loading_is_not_killed_but_deadline_dominates_late_healthy_probe(db: SQLiteDatabase) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        loading = actions.request(uow, start=True, release=release())
        current = STORE.runtime_version(uow)
        assert (
            actions.apply_probe(
                uow,
                expected_version=current,
                result=RuntimeProbe(False, False, False, "model_loading"),
                load_expired=False,
            )
            == loading
        )
        assert STORE.runtime_version(uow) == current
        late = actions.apply_probe(
            uow, expected_version=current, result=RuntimeProbe(True, True, True, "stub"), load_expired=True
        )
        assert (
            late is not None
            and late.runtime.state == RuntimeState.UNAVAILABLE
            and late.error_code == ErrorCode.MODEL_TIMEOUT
        )


@pytest.mark.trace("T-E13-075", "T-E13-065")
def test_running_transient_failures_count_then_success_resets_threshold(db: SQLiteDatabase) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        actions.request(uow, start=True, release=release())
        actions.apply_probe(
            uow,
            expected_version=STORE.runtime_version(uow),
            result=RuntimeProbe(True, True, True, "stub"),
            load_expired=False,
        )
        for expected in (1, 2):
            failed = actions.apply_probe(
                uow,
                expected_version=STORE.runtime_version(uow),
                result=RuntimeProbe(False, False, False, "model_unavailable"),
                load_expired=False,
            )
            assert (
                failed is not None
                and failed.runtime.state == RuntimeState.RUNNING
                and failed.runtime.failed_probes == expected
            )
        healthy = actions.apply_probe(
            uow,
            expected_version=STORE.runtime_version(uow),
            result=RuntimeProbe(True, True, True, "stub"),
            load_expired=False,
        )
        assert healthy is not None and healthy.runtime.failed_probes == 0
        for _ in range(3):
            actions.apply_probe(
                uow,
                expected_version=STORE.runtime_version(uow),
                result=RuntimeProbe(False, False, False, "model_unavailable"),
                load_expired=False,
            )
        assert actions.current(uow).runtime.state == RuntimeState.UNAVAILABLE


@pytest.mark.trace("T-E13-075", "T-E13-060")
def test_restart_preserves_desired_identity_but_requires_new_loading_generation(db: SQLiteDatabase) -> None:
    actions = RuntimeActions(STORE, profile="eval", health_fail_threshold=3)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        actions.boot(uow)
        actions.request(uow, start=True, release=release("live"))
        actions.apply_probe(
            uow,
            expected_version=STORE.runtime_version(uow),
            result=RuntimeProbe(True, True, True, "ok"),
            load_expired=False,
        )
        previous = STORE.runtime_version(uow)
        rebooted = actions.boot(uow)
        assert (
            rebooted.runtime.state == RuntimeState.LOADING
            and rebooted.model_release_id == "model_A"
            and rebooted.mode == "live"
        )
        assert STORE.runtime_version(uow) == previous + 1
