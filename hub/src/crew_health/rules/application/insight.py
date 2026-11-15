"""Insight port and assistant guard (E24 4.2, 3.13, DE24-9): the assistant reaches warnings only here.

The guard has no success type for an action: the assistant can never acknowledge, resolve, dismiss, clear or lower a
warning, and never raise act or emergency (AGENTS 1 rule 4). The domain enforces it; this layer audits the rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import UnitOfWork
from crew_health.rules.application import core
from crew_health.rules.application.core import Runtime, note_audit
from crew_health.rules.application.ports import Allowed, AssistantContext, Rejected
from crew_health.rules.domain.inputs import InsightDraft, InsightInput, RecordFacts
from crew_health.rules.domain.vocabulary import ASSISTANT_LEVELS, Level


@dataclass(slots=True)
class AssistantGuard:
    rt: Runtime

    def propose_level(
        self, uow: UnitOfWork, ctx: AssistantContext, level: str, topic_id: str | None
    ) -> Rejected | Allowed:
        try:
            parsed = Level(level)
        except ValueError:
            parsed = None
        policy = self.rt.facts(uow).policy
        if parsed is None or parsed not in ASSISTANT_LEVELS or parsed not in policy.allowed_warning_levels:
            note_audit(self.rt, uow, "warning.assistant_rejected", {}, {"code": "LEVEL_NOT_ALLOWED"})
            return Rejected("LEVEL_NOT_ALLOWED")
        return Allowed("info" if parsed is Level.INFO else "watch")

    def propose_action(
        self, uow: UnitOfWork, ctx: AssistantContext, warning_id: str, action: str
    ) -> Rejected:
        note_audit(self.rt, uow, "warning.assistant_rejected", {}, {"code": "ACTION_NOT_ALLOWED"})
        return Rejected("ACTION_NOT_ALLOWED")


def validate_and_raise(
    rt: Runtime, uow: UnitOfWork, draft: InsightDraft, facts: dict[tuple[str, str], RecordFacts]
) -> None:
    """UC-R5. A violation stores nothing and is audited with codes only; the model's own level is never trusted."""
    from crew_health.rules.domain.inputs import EvidenceKey

    keyed = tuple((EvidenceKey(k[0], k[1]), v) for k, v in facts.items())  # type: ignore[arg-type]
    policy = rt.facts(uow).policy
    if rt.binding is None:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "template_renderer_absent", retryable=True)
    core.run_input(rt, uow, InsightInput(draft, keyed, policy))
