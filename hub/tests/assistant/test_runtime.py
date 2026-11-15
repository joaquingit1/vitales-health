import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.runtime import Runtime, RuntimeState, command, probe


@pytest.mark.trace("T-E13-063", "T-E13-065", "V69")
def test_runtime_start_loading_probe_failures_and_stop_follow_authoritative_state() -> None:
    runtime = command(Runtime(False, RuntimeState.STOPPED), start=True)
    assert runtime.state == RuntimeState.LOADING and runtime.desired
    assert command(runtime, start=True) == runtime
    loading = probe(
        runtime, healthy=False, identity_ok=False, worker_exited=False, load_expired=False, threshold=3
    )
    assert loading.state == RuntimeState.LOADING
    ready = probe(
        loading, healthy=True, identity_ok=True, worker_exited=False, load_expired=False, threshold=3
    )
    assert ready.state == RuntimeState.RUNNING
    failed = probe(
        ready, healthy=True, identity_ok=False, worker_exited=False, load_expired=False, threshold=3
    )
    assert failed.state == RuntimeState.UNAVAILABLE
    assert command(failed, start=True).state == RuntimeState.LOADING
    assert command(ready, start=False) == Runtime(False, RuntimeState.STOPPED)


@pytest.mark.trace("T-E13-065", "INV-E13-14")
@given(threshold=st.integers(min_value=1, max_value=32))
def test_exact_failed_probe_threshold_stops_runtime_and_success_resets_counter(threshold: int) -> None:
    runtime = Runtime(True, RuntimeState.RUNNING)
    for expected in range(1, threshold):
        runtime = probe(
            runtime,
            healthy=False,
            identity_ok=False,
            worker_exited=False,
            load_expired=False,
            threshold=threshold,
        )
        assert runtime.state == RuntimeState.RUNNING and runtime.failed_probes == expected
    recovered = probe(
        runtime, healthy=True, identity_ok=True, worker_exited=False, load_expired=False, threshold=threshold
    )
    assert recovered.failed_probes == 0
    failed = probe(
        runtime,
        healthy=False,
        identity_ok=False,
        worker_exited=False,
        load_expired=False,
        threshold=threshold,
    )
    assert failed.state == RuntimeState.UNAVAILABLE


@pytest.mark.trace("T-E13-063", "T-E13-065")
@pytest.mark.parametrize("worker_exited,load_expired", [(True, False), (False, True)])
def test_loading_has_finite_deadline_and_worker_exit_is_terminal(
    worker_exited: bool, load_expired: bool
) -> None:
    state = probe(
        Runtime(True, RuntimeState.LOADING),
        healthy=False,
        identity_ok=False,
        worker_exited=worker_exited,
        load_expired=load_expired,
        threshold=3,
    )
    assert state.state == RuntimeState.UNAVAILABLE


@pytest.mark.trace("T-E13-064", "V69")
def test_stopped_runtime_cannot_be_started_by_spurious_probe() -> None:
    runtime = Runtime(False, RuntimeState.STOPPED)
    assert (
        probe(runtime, healthy=True, identity_ok=True, worker_exited=False, load_expired=False, threshold=3)
        == runtime
    )
    with pytest.raises(AssistantRefused):
        probe(runtime, healthy=True, identity_ok=True, worker_exited=False, load_expired=False, threshold=0)
