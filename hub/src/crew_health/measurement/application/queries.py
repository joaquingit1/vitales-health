"""Authorized bounded projections: capabilities (CapabilityQueries) and one request (getMeasurementRequest)."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Audience, ReadSnapshot, ReadSnapshotFactory, SessionContext
from crew_health.measurement.application.access import can_read, is_crew_person, viewer_of
from crew_health.measurement.application.ports import (
    AdapterReadinessPort,
    CapabilityView,
    ConsentQueries,
    KitCapabilitiesPort,
    KitCapability,
    MissionStatePort,
    View,
)
from crew_health.measurement.application.representation import request_wire
from crew_health.measurement.application.store import MeasurementStore
from crew_health.measurement.domain.model import capability_status


def _unknown(detail: str) -> ProblemError:
    """Inputs needed to compute availability are missing: fail loud and retryable, never a guessed list."""
    return ProblemError(ErrorCode.INTERNAL_ERROR, detail, retryable=True)


def overlay(capability: KitCapability, ready_methods: frozenset[str] | None) -> CapabilityView:
    status, reason = capability_status(
        kit_available=capability.kit_status == "available",
        methods=capability.methods,
        ready_methods=ready_methods,
    )
    return CapabilityView(
        code=capability.code,
        label=capability.label,
        methods=capability.methods,
        device_ids=(),
        instrument_ids=capability.instrument_ids,
        missing_instrument_ids=capability.missing_instrument_ids,
        operator_requirement=capability.operator_requirement,
        applicable_environments=capability.applicable_environments,
        status=status,
        unavailable_reason=reason,
        expected_duration_seconds=capability.expected_duration_seconds,
        body_region=capability.body_region,
    )


class CapabilityService:
    """CapabilityQueries provider. Operational facts only: no person, overlay or health data (E05 3)."""

    def __init__(
        self,
        *,
        kit_capabilities: KitCapabilitiesPort | None,
        mission: MissionStatePort | None,
        readiness: AdapterReadinessPort | None,
        max_capabilities: int,
    ) -> None:
        if not 1 <= max_capabilities <= 1024:
            raise ValueError("measurement.max_capabilities outside 1 to 1024")
        self._kit = kit_capabilities
        self._mission = mission
        self._readiness = readiness
        self._max = max_capabilities

    def list(self, uow: View) -> tuple[CapabilityView, ...]:
        if self._kit is None or self._mission is None:
            raise _unknown("capability_inputs_unavailable")
        facts = self._mission.current(uow)
        if facts is None:
            raise _unknown("environment_unknown")
        packed = self._kit.capabilities(uow, facts.active_environment_id)
        if len(packed) > self._max:
            raise _unknown("measurement.max_capabilities_exceeded")
        ready = None if self._readiness is None else self._readiness.ready_methods(uow)
        return tuple(overlay(c, ready) for c in packed)

    def get(self, uow: View, code: str) -> CapabilityView | None:
        for capability in self.list(uow):
            if capability.code == code:
                return capability
        return None


@dataclass(frozen=True, slots=True)
class CapabilityPage:
    items: tuple[CapabilityView, ...]
    next_cursor: str | None
    total: int
    release_id: str


def page(
    items: tuple[CapabilityView, ...], *, cursor: str | None, limit: int
) -> tuple[tuple[CapabilityView, ...], str | None]:
    """Cursor is the last code served; capabilities keep document order, so the cursor is stable per release."""
    start = 0
    if cursor is not None:
        codes = [c.code for c in items]
        if cursor not in codes:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        start = codes.index(cursor) + 1
    chunk = items[start : start + limit]
    more = start + limit < len(items)
    return chunk, (chunk[-1].code if more and chunk else None)


@dataclass(frozen=True, slots=True)
class RequestView:
    body: dict[str, object]
    revision: int
    release_id: str


class MeasurementQueries:
    def __init__(
        self,
        *,
        snapshots: ReadSnapshotFactory,
        store: MeasurementStore,
        capabilities: CapabilityService,
        consent: ConsentQueries | None,
        page_size: int,
    ) -> None:
        if not 1 <= page_size <= 200:
            raise ValueError("measurement.page_size outside 1 to 200")
        self._snapshots = snapshots
        self._store = store
        self._capabilities = capabilities
        self._consent = consent
        self._page_size = page_size

    def list_capabilities(
        self, session: SessionContext, *, cursor: str | None, limit: int | None
    ) -> CapabilityPage:
        size = self._page_size if limit is None else limit
        if not 1 <= size <= 200:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        with self._snapshots.snapshot() as snap:
            items = self._capabilities.list(snap)
            chunk, next_cursor = page(items, cursor=cursor, limit=size)
            return CapabilityPage(chunk, next_cursor, len(items), snap.release.release_id)

    def get_request(self, session: SessionContext, request_id: str) -> RequestView:
        if not is_crew_person(session):
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        with self._snapshots.snapshot() as snap:
            request = self._store.get_request(snap.connection, request_id)
            if request is None:
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            if not can_read(snap, self._consent, request, session):
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
            return RequestView(
                request_wire(request, viewer_of(session)), request.revision, snap.release.release_id
            )


class MeasurementAudienceRule:
    """Event delivery for measurement_request.changed: the same object grant as an HTTP read, checked now."""

    def __init__(self, *, store: MeasurementStore, consent: ConsentQueries | None) -> None:
        self._store = store
        self._consent = consent

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        if audience.rule != AUDIENCE_RULE:
            return False
        request = self._store.get_request(snapshot.connection, audience.object_ref)
        return request is not None and can_read(snapshot, self._consent, request, session)


AUDIENCE_RULE = "measurement.request"
