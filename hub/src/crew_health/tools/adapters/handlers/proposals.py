"""propose_measurement (E12 4.3): a draft for the controller and the person, never an accepted request.

The handler reads E05's capability registry and returns the validated draft with the capability's current status.
It writes nothing: E13 applies an accepted proposal through EffectApplier and E05's MeasurementCommands in its own
task completion unit of work, after a human accepts it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import UnitOfWork
from crew_health.measurement.application.ports import CapabilityQueries
from crew_health.tools.application.ports import (
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    HandlerUnavailable,
)
from crew_health.tools.domain.scope import ToolScope

_DRAFT_KEYS = frozenset(
    {"capability_code", "reason", "decision_affected", "body_region", "condition_ids", "source_ids"}
)


def _strings(value: object) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(value)


class ProposeMeasurementHandler:
    name = "propose_measurement"
    effect: Literal["proposal"] = "proposal"

    def __init__(self, capabilities: Callable[[], CapabilityQueries | None]) -> None:
        self._capabilities = capabilities

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, object], deadline: ElapsedDeadline
    ) -> HandlerOutcome:
        code, reason, decision = (
            arguments.get("capability_code"),
            arguments.get("reason"),
            arguments.get("decision_affected"),
        )
        region = arguments.get("body_region")
        conditions, sources = _strings(arguments.get("condition_ids")), _strings(arguments.get("source_ids"))
        if (
            set(arguments) - _DRAFT_KEYS
            or not isinstance(code, str)
            or not isinstance(reason, str)
            or not isinstance(decision, str)
            or not (region is None or isinstance(region, str))
            or conditions is None
            or sources is None
        ):
            return HandlerRejected("malformed_schema")
        capabilities = self._capabilities()
        if capabilities is None:
            return HandlerUnavailable(())
        capability = capabilities.get(uow, code)
        if capability is None:
            return HandlerRejected("handler_rejected")
        body = {
            "draft": {
                "capability_code": code,
                "reason": reason,
                "decision_affected": decision,
                "body_region": region,
                "condition_ids": conditions,
                "source_ids": sources,
            },
            "capability": {
                "code": capability.code,
                "status": capability.status,
                "unavailable_reason": capability.unavailable_reason,
            },
        }
        return HandlerOk(canonical_json(body), (), None, ())
