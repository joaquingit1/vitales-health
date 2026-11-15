from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Event, Thread

import pytest

from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.service import (
    ForecastRequest,
    ForecastResponse,
    ForecastService,
    SeriesPoint,
    UnavailableResponse,
)
from crew_health_forecast.domain.result import (
    ForecastStep,
    InvalidModelOutput,
    prediction_steps,
    validate_steps,
)

from .conftest import FakeBackend


@pytest.mark.trace("E14-03", "V32", "V69")
def test_ready_roundtrip_preserves_units_and_decimal_text(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    service = ForecastService(policy, backend, 1)
    result = service.forecast(forecast_request, 2)
    assert isinstance(result, ForecastResponse)
    assert result.unit == forecast_request.unit
    assert result.interval_level == "0.8"
    assert result.context_days == policy.min_context_days
    assert len(result.steps) == policy.horizon_days
    assert [step.mission_day for step in result.steps] == list(
        range(policy.min_context_days + 1, policy.min_context_days + policy.horizon_days + 1)
    )
    assert result.steps[0].median == "14.25"
    assert backend.context[-1] == Decimal("14.25")
    assert result.model.id == "test/model"
    assert service.health().state == "ready"


@pytest.mark.trace("E14-03", "V32", "V69")
@pytest.mark.parametrize(
    "reason",
    [
        "weights_absent",
        "model_hash_unpinned",
        "weights_hash_mismatch",
        "model_package_absent",
        "model_load_failed",
    ],
)
def test_backend_unavailable(policy: ForecastPolicy, forecast_request: ForecastRequest, reason: str) -> None:
    service = ForecastService(policy, FakeBackend(reason), 1)
    assert service.forecast(forecast_request, 2) == UnavailableResponse(
        request_id=forecast_request.request_id, reason=reason
    )
    assert service.health().reason == reason
    assert service.health().state == "unavailable"


@pytest.mark.trace("E14-03", "V32")
def test_mismatch_and_series_validation(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    service = ForecastService(policy, backend, 1)
    mismatch = forecast_request.model_copy(update={"policy_version": "different"})
    assert service.forecast(mismatch, 2) == UnavailableResponse(
        request_id=forecast_request.request_id, reason="policy_version_mismatch"
    )
    empty = forecast_request.model_copy(update={"series": []})
    assert service.forecast(empty, 2) == UnavailableResponse(
        request_id=forecast_request.request_id, reason="empty"
    )
    assert backend.calls == 0
    assert isinstance(
        service.forecast(forecast_request.model_copy(update={"policy_version": None}), 2), ForecastResponse
    )


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    ("fail", "invalid", "reason"), [(True, False, "inference_failed"), (False, True, "model_output_invalid")]
)
def test_backend_failure_and_bad_output_release_slot(
    policy: ForecastPolicy,
    backend: FakeBackend,
    forecast_request: ForecastRequest,
    fail: bool,
    invalid: bool,
    reason: str,
) -> None:
    service = ForecastService(policy, backend, 1)
    backend.fail, backend.invalid = fail, invalid
    assert service.forecast(forecast_request, 2) == UnavailableResponse(
        request_id=forecast_request.request_id, reason=reason
    )
    backend.fail = backend.invalid = False
    assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)


@pytest.mark.trace("E14-03", "V69")
def test_bounded_nonblocking_inference(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    backend.release = Event()
    service = ForecastService(policy, backend, 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.forecast, forecast_request, 2)
        try:
            assert backend.started.wait(timeout=2)
            assert service.forecast(forecast_request, 2) == UnavailableResponse(
                request_id=forecast_request.request_id, reason="busy"
            )
            assert backend.calls == 1
        finally:
            backend.release.set()
        assert isinstance(future.result(timeout=2), ForecastResponse)
    assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)


@pytest.mark.trace("E14-03", "R12")
def test_service_context_uses_latest(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    policy = replace(policy, max_context_days=policy.min_context_days)
    series = forecast_request.series + [SeriesPoint(mission_day=15, value="15.25")]
    result = ForecastService(policy, backend, 1).forecast(
        forecast_request.model_copy(update={"series": series}), 2
    )
    assert isinstance(result, ForecastResponse)
    assert result.context_days == 14
    assert backend.context[0] == Decimal("2.25")


@pytest.mark.trace("E14-03", "V32")
@pytest.mark.parametrize(
    "steps",
    [
        [],
        [ForecastStep(2, Decimal(0), Decimal(1), Decimal(2))],
        [ForecastStep(15, Decimal("NaN"), Decimal(1), Decimal(2))],
        [ForecastStep(15, Decimal(2), Decimal(1), Decimal(3))],
    ],
)
def test_output_validation(steps: list[ForecastStep]) -> None:
    with pytest.raises(InvalidModelOutput):
        validate_steps(steps, 14, 1)


@pytest.mark.trace("E14-03", "V32")
def test_valid_degenerate_interval() -> None:
    validate_steps([ForecastStep(15, Decimal(1), Decimal(1), Decimal(1))], 14, 1)
    assert prediction_steps([(Decimal(1), Decimal(1), Decimal(1))], 14, 1)[0].mission_day == 15


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize(("inflight", "abandoned"), [(0, None), (-1, 1), (1, 0), (1, -1)])
def test_invalid_inflight_bound(
    policy: ForecastPolicy, backend: FakeBackend, inflight: int, abandoned: int | None
) -> None:
    with pytest.raises(ValueError, match="invalid_inflight_bound"):
        ForecastService(policy, backend, inflight, max_abandoned=abandoned)


@pytest.mark.trace("E14-03", "V69")
def test_abandoned_calls_are_bounded_so_threads_stay_bounded(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    backend.release = Event()
    service = ForecastService(policy, backend, 1, max_abandoned=1)
    try:
        for index in range(2):
            request = forecast_request.model_copy(update={"request_id": f"timeout-{index}"})
            assert service.forecast(request, 0.1) == UnavailableResponse(
                request_id=request.request_id, reason="timeout"
            )
            assert backend.calls == index + 1
            assert all(thread.is_alive() for thread in backend.threads)
            assert all(thread.daemon for thread in backend.threads)
        assert service.forecast(forecast_request, 2) == UnavailableResponse(
            request_id=forecast_request.request_id, reason="busy"
        )
        assert backend.calls == 2
    finally:
        backend.release.set()
        for thread in backend.threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
    assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)
    assert backend.calls == 3


@pytest.mark.trace("E14-03", "V69")
def test_completion_racing_timeout_returns_the_finished_result(
    policy: ForecastPolicy,
    backend: FakeBackend,
    forecast_request: ForecastRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_wait = Event.wait

    def expired_wait(event: Event, timeout: float | None = None) -> bool:
        assert original_wait(event, timeout=2)
        return False

    service = ForecastService(policy, backend, 1)
    with monkeypatch.context() as patch:
        patch.setattr(Event, "wait", expired_wait)
        assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)
    assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)
    assert backend.calls == 2


@pytest.mark.trace("E14-03", "V69")
def test_thread_start_failure_releases_admission(
    policy: ForecastPolicy,
    backend: FakeBackend,
    forecast_request: ForecastRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(thread: Thread) -> None:
        raise RuntimeError("thread_start_failed")

    service = ForecastService(policy, backend, 1)
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", fail_start)
        assert service.forecast(forecast_request, 2) == UnavailableResponse(
            request_id=forecast_request.request_id, reason="inference_failed"
        )
        assert backend.calls == 0
    assert isinstance(service.forecast(forecast_request, 2), ForecastResponse)
