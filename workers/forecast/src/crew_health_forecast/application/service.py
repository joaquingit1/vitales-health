"""Bounded experimental forecasts with strict decimal-text wire models."""

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.ports import BackendStatus, ModelBackend
from crew_health_forecast.domain.result import InvalidModelOutput, prediction_steps
from crew_health_forecast.domain.series import QualifiedSeries, RawPoint, Unqualified, parse_series


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SeriesPoint(WireModel):
    mission_day: Annotated[int, Field(ge=1)]
    value: str


class ForecastRequest(WireModel):
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    policy_version: str | None = None
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    series: list[SeriesPoint]


class ModelInfo(WireModel):
    id: str
    revision: str
    sha256: str | None


class StepResponse(WireModel):
    mission_day: int
    low: str
    median: str
    high: str


class ForecastResponse(WireModel):
    request_id: str
    status: Literal["forecast"] = "forecast"
    unit: str
    model: ModelInfo
    policy_version: str
    interval_level: str
    context_days: int
    steps: list[StepResponse]


class UnavailableResponse(WireModel):
    request_id: str
    status: Literal["unavailable"] = "unavailable"
    reason: str


class HealthResponse(WireModel):
    state: Literal["ready", "unavailable"]
    reason: str | None
    policy_version: str
    model: ModelInfo | None


type Response = ForecastResponse | UnavailableResponse


@dataclass
class _ForecastCall:
    result: Response
    event: Event = field(default_factory=Event)
    finished: bool = False
    abandoned: bool = False


class ForecastService:
    def __init__(
        self,
        policy: ForecastPolicy,
        backend: ModelBackend,
        max_inflight: int,
        *,
        max_abandoned: int | None = None,
    ) -> None:
        if max_abandoned is None:
            max_abandoned = max_inflight
        if max_inflight < 1 or max_abandoned < 1:
            raise ValueError("invalid_inflight_bound")
        self.policy = policy
        self.backend = backend
        self._max_inflight = max_inflight
        self._max_threads = max_inflight + max_abandoned
        self._inflight = 0
        self._abandoned = 0
        self._lock = Lock()

    def health(self) -> HealthResponse:
        status = self.backend.status()
        return HealthResponse(
            state="ready" if status.ready else "unavailable",
            reason=status.reason,
            policy_version=self.policy.version,
            model=ModelInfo(id=status.model_id, revision=status.revision, sha256=status.sha256),
        )

    def forecast(self, request: ForecastRequest, timeout_s: float) -> Response:
        if request.policy_version is not None and request.policy_version != self.policy.version:
            return UnavailableResponse(request_id=request.request_id, reason="policy_version_mismatch")
        status = self.backend.status()
        if not status.ready:
            return UnavailableResponse(
                request_id=request.request_id, reason=status.reason or "model_unavailable"
            )
        raw: list[RawPoint] = [
            {"mission_day": point.mission_day, "value": point.value} for point in request.series
        ]
        qualified = parse_series(raw, request.unit, self.policy)
        if isinstance(qualified, Unqualified):
            return UnavailableResponse(request_id=request.request_id, reason=qualified.reason)
        with self._lock:
            if self._inflight >= self._max_inflight or self._inflight + self._abandoned >= self._max_threads:
                return UnavailableResponse(request_id=request.request_id, reason="busy")
            self._inflight += 1
        call = _ForecastCall(UnavailableResponse(request_id=request.request_id, reason="inference_failed"))
        try:
            Thread(target=self._run_call, args=(call, request, qualified, status), daemon=True).start()
        except RuntimeError, OSError:
            with self._lock:
                self._inflight -= 1
            return call.result
        call.event.wait(timeout_s)
        with self._lock:
            if call.finished:
                return call.result
            call.abandoned = True
            self._inflight -= 1
            self._abandoned += 1
        return UnavailableResponse(request_id=request.request_id, reason="timeout")

    def _run_call(
        self, call: _ForecastCall, request: ForecastRequest, qualified: QualifiedSeries, status: BackendStatus
    ) -> None:
        result = call.result
        try:
            result = self._predict(request, qualified, status)
        except Exception:
            result = UnavailableResponse(request_id=request.request_id, reason="inference_failed")
        finally:
            with self._lock:
                if call.abandoned:
                    self._abandoned -= 1
                else:
                    self._inflight -= 1
                    call.result = result
                call.finished = True
                call.event.set()

    def _predict(
        self, request: ForecastRequest, qualified: QualifiedSeries, status: BackendStatus
    ) -> Response:
        try:
            prediction = self.backend.predict(
                tuple(point.value for point in qualified.points),
                self.policy.horizon_days,
                self.policy.low,
                self.policy.high,
            )
        except Exception:
            return UnavailableResponse(request_id=request.request_id, reason="inference_failed")
        try:
            steps = prediction_steps(prediction, qualified.points[-1].mission_day, self.policy.horizon_days)
        except InvalidModelOutput:
            return UnavailableResponse(request_id=request.request_id, reason="model_output_invalid")
        return ForecastResponse(
            request_id=request.request_id,
            unit=qualified.unit,
            model=ModelInfo(id=status.model_id, revision=status.revision, sha256=status.sha256),
            policy_version=self.policy.version,
            interval_level=str(self.policy.high - self.policy.low),
            context_days=qualified.context_days,
            steps=[
                StepResponse(
                    mission_day=step.mission_day,
                    low=str(step.low),
                    median=str(step.median),
                    high=str(step.high),
                )
                for step in steps
            ],
        )
