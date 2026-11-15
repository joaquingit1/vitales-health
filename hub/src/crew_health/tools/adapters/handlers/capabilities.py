from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Literal

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork
from crew_health.measurement.application.ports import CapabilityQueries
from crew_health.tools.application.ports import HandlerOk, HandlerOutcome, HandlerRejected, HandlerUnavailable
from crew_health.tools.domain.scope import ToolScope


class CapabilitiesHandler:
    name = "get_capabilities"
    effect: Literal["read"] = "read"

    def __init__(self, capabilities: CapabilityQueries | None, settings: HubSettings) -> None:
        self._capabilities = capabilities
        self._maximum = settings.integer("tools.max_array_items")

    def handle(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        arguments: Mapping[str, object],
        deadline: ElapsedDeadline,
    ) -> HandlerOutcome:
        codes = arguments.get("codes")
        if not isinstance(codes, list) or len(codes) > self._maximum or set(arguments) != {"codes"}:
            return HandlerRejected("malformed_schema")
        if not all(isinstance(code, str) for code in codes):
            return HandlerRejected("malformed_schema")
        if self._capabilities is None:
            return HandlerUnavailable(())
        capabilities = self._capabilities.list(uow)
        if len(capabilities) > self._maximum:
            return HandlerRejected("context_over_budget")
        by_code = {item.code: item for item in capabilities}
        if any(code not in by_code for code in codes):
            return HandlerRejected("handler_rejected")
        selected = tuple(by_code[code] for code in sorted(set(codes))) if codes else capabilities
        return HandlerOk(canonical_json({"items": [asdict(item) for item in selected]}), (), None, ())
