"""Evidence tools over the pinned E10 release (E12 4.2): read_source_passage and search_evidence.

Reading a passage needs no physical resource, so its guidance request names none; E27 still decides whether the
passage may be shown (clinical review, release, origin). search_evidence stays unavailable until a verified public
query vocabulary is bound (E12 4.1, R-E12-2): the model's free text never reaches E10.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Literal

from crew_health.guidance.application.ports import Requirements
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork
from crew_health.knowledge.application.ports import dto  # type: ignore[attr-defined]
from crew_health.tools.application.ports import (
    HandlerNoResult,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    HandlerUnavailable,
)
from crew_health.tools.application.retrieval_service import RetrievalService
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import RetrievalResult
from crew_health.tools.domain.scope import ToolScope

_READ_USE: dto.AllowedUse = "risk_context"
_NO_REQUIREMENTS = Requirements(frozenset(), {}, frozenset(), "any_crew", None)

type ServiceSource = Callable[[ToolScope], RetrievalService | None]


def _outcome(result: RetrievalResult) -> HandlerOutcome:
    if result.status == "unavailable":
        return HandlerUnavailable(result.limitations)
    if result.status == "no_result" and not result.history:
        return HandlerNoResult(result.limitations)
    body = {
        "passages": [asdict(passage) for passage in result.passages],
        "history": [asdict(passage) for passage in result.history],
    }
    provenance = tuple(sorted({passage.passage_id for passage in result.passages}))
    return HandlerOk(canonical_json(body), provenance, None, result.limitations)


def _guarded(run: Callable[[], RetrievalResult]) -> HandlerOutcome:
    try:
        return _outcome(run())
    except ToolsInvalid as error:
        if error.code == "CAPABILITY_UNAVAILABLE":
            return HandlerUnavailable(())
        if error.code in {"MODEL_OUTPUT_REJECTED", "MALFORMED_MODEL_OUTPUT", "PAYLOAD_TOO_LARGE"}:
            return HandlerRejected("handler_rejected")
        raise


class ReadPassageHandler:
    name = "read_source_passage"
    effect: Literal["read"] = "read"

    def __init__(self, services: ServiceSource, settings: HubSettings) -> None:
        self._services = services
        self._neighbors = settings.integer("tools.retrieval.neighbors_max")

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, object], deadline: ElapsedDeadline
    ) -> HandlerOutcome:
        passage_id, include_parent = arguments.get("passage_id"), arguments.get("include_parent")
        if set(arguments) != {"passage_id", "include_parent"} or not isinstance(passage_id, str):
            return HandlerRejected("malformed_schema")
        if type(include_parent) is not bool:
            return HandlerRejected("malformed_schema")
        service = self._services(scope)
        if service is None:
            return HandlerUnavailable(())
        return _guarded(
            lambda: service.read(
                uow,
                scope,
                passage_id,
                use=_READ_USE,
                filters=dto.ApplicabilityFilter(),
                requirements=_NO_REQUIREMENTS,
                patient_location_id=None,
                proposed_operator_id=None,
                neighbors=self._neighbors if include_parent else 0,
            )
        )


class SearchEvidenceHandler:
    name = "search_evidence"
    effect: Literal["read"] = "read"

    def __init__(self, services: ServiceSource) -> None:
        self._services = services

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, object], deadline: ElapsedDeadline
    ) -> HandlerOutcome:
        query, filters = arguments.get("query"), arguments.get("filters")
        if set(arguments) != {"query", "filters"} or not isinstance(query, str) or filters != {}:
            return HandlerRejected("malformed_schema")
        service = self._services(scope)
        if service is None:
            return HandlerUnavailable(())
        return _guarded(
            lambda: service.search(
                uow,
                scope,
                query,
                use=_READ_USE,
                filters=dto.ApplicabilityFilter(),
                requirements=_NO_REQUIREMENTS,
                patient_location_id=None,
                proposed_operator_id=None,
            )
        )
