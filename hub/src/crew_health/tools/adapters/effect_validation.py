"""E12's preflight of an accepted measurement proposal before E05 sees it (E12 4.3).

E05 remains the validator of capability, subject, consent, origin, request gate, kit and reason. E12 only refuses a
proposal whose session is no longer current or whose capability E05's registry does not list, so a stale or
invented proposal never reaches the owner command.
"""

from __future__ import annotations

from collections.abc import Callable

from crew_health.contracts_gen.schemas.model_action_schema import ModelAction, ModelAction2
from crew_health.kernel.uow import UnitOfWork
from crew_health.measurement.application.ports import CapabilityQueries
from crew_health.tools.application.ports import ScopeAuthority
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.scope import ToolScope


class CapabilityEffectValidation:
    def __init__(
        self, capabilities: Callable[[], CapabilityQueries | None], authority: ScopeAuthority
    ) -> None:
        self._capabilities, self._authority = capabilities, authority

    def validate(self, uow: UnitOfWork, scope: ToolScope, action: ModelAction) -> None:
        value = action.root
        if not isinstance(value, ModelAction2):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        capabilities = self._capabilities()
        if capabilities is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        if not self._authority.require_current(uow, scope):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if capabilities.get(uow, value.capability_code.root) is None:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
