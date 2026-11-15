"""The product-suite system under test: one fresh evaluation hub per case, driven through its public API.

Design 4.5 (system and rules-only tracks), 5.6 (hub driver), 6.1 (operations) and INV-E18-10. Every response
is parsed with the hub's generated contract models before it is read. The trace holds what the public API
shows: warnings, help sessions, measurement requests and their kit checks, capability gaps, answers, the
assessment and the recorder count between the case's marks. Tool calls, denied calls and their side effects
are not visible through any interface the hub or E21 declares, so those trace fields stay unknown and their
checks `not_graded`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from crew_health.contracts_gen.schemas.assessment_schema import Assessment
from crew_health.contracts_gen.schemas.config_index_schema import ConfigIndex
from crew_health.contracts_gen.schemas.context_schema import Context
from crew_health.contracts_gen.schemas.episode_schema import Episode
from crew_health.contracts_gen.schemas.event_page_schema import EventPage
from crew_health.contracts_gen.schemas.job_schema import Job
from crew_health.contracts_gen.schemas.measurement_request_schema import MeasurementRequest
from crew_health.contracts_gen.schemas.session_schema import Session
from crew_health.contracts_gen.schemas.warning_schema import Warning
from crew_health.kernel.clock import Clock
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity, Unit
from pydantic import BaseModel, ValidationError

from crew_health_bench.adapters.documents import BenchSettings
from crew_health_bench.adapters.isolation import require_evaluation_hub
from crew_health_bench.application.harness_ports import EvaluationHub, HubResponse, ProductHarness
from crew_health_bench.application.ports import Simulator
from crew_health_bench.domain.documents import object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.trace import CaseTrace
from crew_health_bench.domain.visible import VisibleCase

# Wire error codes and how a case treats them (design 5.6, 6.1).
_HARNESS_ERRORS = frozenset({"INTERNAL_ERROR", "UNAUTHENTICATED", "ORIGIN_MISMATCH"})
_TRACE_FACTS = frozenset(
    {
        "MODEL_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "MODEL_OUTPUT_REJECTED",
        "MALFORMED_MODEL_OUTPUT",
        "KIT_CANNOT_PERFORM",
    }
)
_TERMINAL_JOBS = frozenset({"succeeded", "failed", "cancelled"})


@dataclass
class _Case:
    """The mutable state of one driven case; it never outlives `run_case`."""

    hub: EvaluationHub
    session: str = ""
    calls: int = 0
    attempts: dict[str, str] = field(default_factory=dict)


class HubDriver:
    def __init__(
        self,
        *,
        harness: ProductHarness,
        settings: BenchSettings,
        clock: Clock,
        ids: IdFactory,
        run_id: str,
        sleep: Callable[[Quantity], None],
        config_release_id: str,
        model_release_id: str,
    ) -> None:
        self.config_release_id, self.model_release_id = config_release_id, model_release_id
        self.harness, self.settings, self.clock, self.ids, self.run_id = harness, settings, clock, ids, run_id
        self.sleep = sleep
        self.timeout = Quantity(settings.quantity("bench.hub.call_timeout").value * 1000, Unit("ms"))
        self.max_calls = settings.integer("bench.case.max_hub_calls")
        self.max_events = settings.integer("bench.case.max_events")
        self.retries = settings.integer("bench.hub.max_retries")
        self.maximum = settings.integer("bench.store.max_line_bytes")

    def run_case(
        self, case: VisibleCase, simulator: Simulator, *, track: str, deadline: Quantity
    ) -> CaseTrace:
        if deadline.unit.code != "s" or deadline.value <= 0:
            raise BenchError("case_invalid")
        hub = self.harness.start("evaluation", run_id=self.run_id)
        try:
            state = _Case(hub)
            state.session = self._session(state, "crew", hub.credentials.crew_credential)
            context = self._parse(Context, self._call(state, "getContext", mutation=False))
            require_evaluation_hub(
                context, hub.hub.data_root, run_id=self.run_id, max_file_bytes=self.maximum
            )
            self._require_release(state)
            hub.seed(case.seed_steps)
            if track == "rules_only":
                self._stop_model(state)
            start = hub.gate.mark(case.case_id + ":start")
            limit = self.clock.monotonic_ns() + int(deadline.value * Decimal(1_000_000_000))
            return self._drive(state, case, simulator, start=start, limit=limit)
        finally:
            self.harness.stop(hub)

    def _require_release(self, state: _Case) -> None:
        """The hub runs the manifest's configuration and model releases, measured at case start (R22, 8.1)."""
        index = self._parse(ConfigIndex, self._call(state, "getConfigIndex", mutation=False))
        model = index.model_release_id
        if index.release_id.root != self.config_release_id or (
            model is not None and model.root != self.model_release_id
        ):
            raise BenchError(
                "drift",
                detail="hub runs configuration release "
                + index.release_id.root
                + ", the manifest pins "
                + self.config_release_id,
            )

    def _drive(
        self, state: _Case, case: VisibleCase, simulator: Simulator, *, start: int, limit: int
    ) -> CaseTrace:
        opened = self._parse(
            Episode,
            self._call(
                state, "startEpisode", body={"concern_text": case.opening_complaint, "origin_mode": "SIM"}
            ),
        )
        episode_id = opened.episode_id.root
        for step in case.script:
            self._script(state, step, episode_id)
        requests = [
            self._measure(state, request_id, simulator) for request_id in self._episode(state, episode_id)[1]
        ]
        episode, _ = self._episode(state, episode_id)
        assessment = self._assess(state, episode_id, episode, limit=limit)
        warnings = self._warnings(state)
        recorded = state.hub.gate.requests_between(start, state.hub.gate.mark(case.case_id + ":end"))
        return _trace(episode, requests, assessment, warnings, state.attempts, len(recorded))

    def _call(
        self,
        state: _Case,
        operation: str,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
        session: str | None = None,
        anonymous: bool = False,
        mutation: bool | None = None,
    ) -> HubResponse:
        key = self.ids.new("benchkey") if (body is not None if mutation is None else mutation) else None
        for attempt in range(self.retries + 1):
            state.calls += 1
            if state.calls > self.max_calls:
                raise BenchError("harness_error", detail="max_hub_calls")
            response = state.hub.client.call(
                operation,
                path_params=dict(path or {}),
                query=dict(query or {}),
                body=body,
                idempotency_key=key,
                session=None if anonymous else session or state.session,
                timeout=self.timeout,
            )
            if 200 <= response.status_code < 300:
                return response
            error = object_map(response.body or {})
            code = text(error.get("code", ""))
            if code == "DUPLICATE_COMMAND" and error.get("current_state") is not None:
                return HubResponse(200, object_map(error["current_state"]), response.etag)
            if code in _TRACE_FACTS:
                raise _TraceFact(code)
            if code in _HARNESS_ERRORS or error.get("retryable") is not True or attempt == self.retries:
                raise BenchError(
                    "harness_error", detail=operation + ":" + (code or str(response.status_code))
                )
        raise BenchError("harness_error", detail=operation)

    def _parse[T: BaseModel](self, model: type[T], response: HubResponse) -> T:
        try:
            return model.model_validate(response.body)
        except ValidationError as error:
            raise BenchError("harness_error", detail="contract:" + model.__name__) from error

    def _session(self, state: _Case, kind: str, credential: str) -> str:
        body: dict[str, object] = {"kind": kind, "credential": credential}
        if kind == "crew":
            body["person_id"] = state.hub.credentials.persona_id
        session = self._parse(Session, self._call(state, "createSession", body=body, anonymous=True))
        if session.token is None:
            raise BenchError("harness_error", detail="createSession:token")
        return session.token

    def _stop_model(self, state: _Case) -> None:
        operator = self._session(state, "operator", state.hub.credentials.operator_credential)
        self._call(state, "setAssistantRuntime", body={"action": "stop"}, session=operator)

    def _script(self, state: _Case, step: Mapping[str, object], episode_id: str) -> None:
        path = {
            key: episode_id if value == "{episode_id}" else text(value)
            for key, value in object_map(step["path_params"]).items()
        }
        body = step.get("body")
        query = {key: text(value) for key, value in object_map(step["query"]).items()}
        try:
            self._call(
                state,
                text(step["operation_id"]),
                path=path,
                query=query,
                body=None if body is None else object_map(body),
            )
        except _TraceFact:
            return

    def _episode(self, state: _Case, episode_id: str) -> tuple[Episode, tuple[str, ...]]:
        episode = self._parse(
            Episode, self._call(state, "getEpisode", path={"episode_id": episode_id}, mutation=False)
        )
        return episode, tuple(item.root for item in episode.measurement_request_ids)

    def _measure(self, state: _Case, request_id: str, simulator: Simulator) -> MeasurementRequest:
        path = {"request_id": request_id}
        request = self._parse(
            MeasurementRequest, self._call(state, "getMeasurementRequest", path=path, mutation=False)
        )
        if request.state == "awaiting_acceptance":
            try:
                self._call(state, "actOnMeasurementRequest", path=path, body={"action": "accept"})
            except _TraceFact:
                pass
            request = self._parse(
                MeasurementRequest, self._call(state, "getMeasurementRequest", path=path, mutation=False)
            )
        if request.state in ("accepted", "in_progress"):
            outcome = simulator.measure(request.capability_code.root)
            state.attempts[request_id] = outcome.outcome
            state.calls += 1
            state.hub.sim.post_attempt(request_id, outcome, session=state.session)
            request = self._parse(
                MeasurementRequest, self._call(state, "getMeasurementRequest", path=path, mutation=False)
            )
        return request

    def _assess(self, state: _Case, episode_id: str, episode: Episode, *, limit: int) -> Assessment | None:
        try:
            job = self._parse(
                Job,
                self._call(
                    state,
                    "startAssessment",
                    path={"episode_id": episode_id},
                    body={"state_snapshot_version": episode.revision},
                ),
            )
            while job.state not in _TERMINAL_JOBS:
                if self.clock.monotonic_ns() >= limit:
                    raise BenchError("harness_error", detail="case_deadline")
                self.sleep(self.settings.quantity("bench.hub.poll_interval"))
                job = self._parse(
                    Job, self._call(state, "getJob", path={"job_id": job.job_id.root}, mutation=False)
                )
        except _TraceFact:
            return None
        if job.state != "succeeded" or job.result_ref is None:
            return None
        return self._parse(
            Assessment,
            self._call(
                state, "getAssessment", path={"assessment_id": job.result_ref.ref_id.root}, mutation=False
            ),
        )

    def _warnings(self, state: _Case) -> tuple[Warning, ...]:
        after: str | None = None
        warnings: list[Warning] = []
        seen = 0
        while True:
            query = {"types": "warning.raised,warning.updated"} | ({} if after is None else {"after": after})
            page = self._parse(EventPage, self._call(state, "getEvents", query=query, mutation=False))
            seen += len(page.events)
            if seen > self.max_events:
                raise BenchError("harness_error", detail="max_events")
            for event in page.events:
                warnings.append(self._parse(Warning, HubResponse(200, object_map(event.data), None)))
            if not page.has_more:
                return tuple(warnings)
            after = page.next_cursor


class _TraceFact(Exception):
    """A wire error that is a fact about the product (the controller worked), never a harness failure."""


def _texts(
    assessment: Assessment | None, requests: Sequence[MeasurementRequest], warnings: Sequence[Warning]
) -> tuple[str, ...]:
    texts = [request.reason for request in requests]
    for warning in warnings:
        texts.extend((warning.title, warning.what, warning.why, warning.headline.fragment))
    if assessment is not None:
        texts.extend(line for line, _ in _lines(assessment))
        if assessment.next_step is not None:
            texts.append(assessment.next_step.root)
    return tuple(texts)


def _lines(assessment: Assessment) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """Every finding, hypothesis and missing evidence line with its evidence references, in assessment
    order."""
    rows: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for finding in assessment.findings:
        rows.append((finding.text, tuple((ref.kind, ref.ref_id.root) for ref in finding.evidence)))
    for hypothesis in assessment.hypotheses:
        rows.append((hypothesis.text, tuple((ref.kind, ref.ref_id.root) for ref in hypothesis.evidence)))
    for missing in assessment.missing_evidence:
        rows.append((missing.text, tuple((ref.kind, ref.ref_id.root) for ref in missing.evidence)))
    return tuple(rows)


def _trace(
    episode: Episode,
    requests: Sequence[MeasurementRequest],
    assessment: Assessment | None,
    warnings: Sequence[Warning],
    attempts: Mapping[str, str],
    recorded: int,
) -> CaseTrace:
    produced = tuple(
        observation.root
        for request in requests
        if request.request_id.root in attempts
        for observation in request.result_observation_ids
    )
    accepted = tuple(
        observation.root
        for request in requests
        if attempts.get(request.request_id.root) == "accepted"
        for observation in request.result_observation_ids
    )
    references: tuple[tuple[str, str], ...] | None = None
    if assessment is not None:
        references = tuple(ref for _, refs in _lines(assessment) for ref in refs)
    cited = tuple(ref for kind, ref in references or () if kind == "observation")
    observations = tuple(
        dict.fromkeys(
            (*cited, *(item.root for request in requests for item in request.result_observation_ids))
        )
    )
    return CaseTrace(
        warnings=tuple((item.warning_id.root, item.level.root, item.raised_by.root) for item in warnings),
        help_sessions=tuple(item.root for item in episode.help_session_ids),
        requests_between=tuple(range(1, recorded + 1)),
        requests=tuple(
            (
                request.request_id.root,
                request.capability_code.root,
                request.state,
                not request.kit_check.can,
            )
            for request in requests
        ),
        capability_gaps=tuple(gap.capability_code.root for gap in episode.capability_gaps),
        capability_codes=tuple(request.capability_code.root for request in requests),
        observation_ids=observations,
        provenance_ids=produced,
        references=references,
        accepted_observations=accepted if assessment is not None else None,
        answers=tuple(answer.answer_id.root for answer in episode.answers),
        approved_passages=() if assessment is not None else None,
        evidence_release_id="" if assessment is None else assessment.evidence_release_id.root,
        texts=_texts(assessment, requests, warnings),
        final_action=None if assessment is None else "assessment_recorded",
        missing_evidence=None
        if assessment is None
        else tuple(line.text for line in assessment.missing_evidence),
        hypotheses=None if assessment is None else tuple(line.text for line in assessment.hypotheses),
    )
