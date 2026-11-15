"""Insight port and assistant guard (E24 4.2, 3.13, DE24-9): the assistant reaches warnings only here.

The guard has no success type for an action: the assistant can never acknowledge, resolve, dismiss, clear or lower a
warning, and never raise act or emergency (AGENTS 1 rule 4). The domain enforces it; this layer audits the rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.rules.application import core
from crew_health.rules.application.core import Runtime, note_audit
from crew_health.rules.application.ports import Allowed, AssistantContext, Rejected, WarningFact
from crew_health.rules.application.wire import fact_of
from crew_health.rules.domain.inputs import InsightDraft, InsightExpiredInput, InsightInput
from crew_health.rules.domain.vocabulary import ASSISTANT_LEVELS, Level
from crew_health.rules.domain.warning import PersonActor, dismiss


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


@dataclass(slots=True)
class InsightPort:
    """UC-R5, UC-R6: E13 calls these inside its own unit of work (4.2). The record facts of the evidence come from
    the records owner's `RecordFactsPort`; without it nothing can be verified, so nothing is stored (fail closed)."""

    rt: Runtime

    def validate_and_raise(self, uow: UnitOfWork, draft: InsightDraft) -> WarningFact | None:
        port = self.rt.record_facts
        if port is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "record_facts_absent", retryable=True)
        found = port.facts(uow, draft.evidence)
        keyed = tuple((key, found[key]) for key in draft.evidence if key in found)
        core.run_input(self.rt, uow, InsightInput(draft, keyed, self.rt.facts(uow).policy))
        raised = self.rt.warnings.for_insight(uow.connection, draft.insight_id)
        return None if raised is None else fact_of(raised)

    def on_insight_expired(self, uow: UnitOfWork, insight_id: str) -> None:
        """The insight ended on its own: the warning clears `expired` only through the engine's expiry rule."""
        w = self.rt.warnings.for_insight(uow.connection, insight_id)
        if w is not None and w.active:
            elapsed = max(0, int((uow.now - w.raised_at).total_seconds()))
            core.run_input(self.rt, uow, InsightExpiredInput(insight_id, elapsed))

    def on_insight_dismissed(self, uow: UnitOfWork, insight_id: str, actor: Actor) -> None:
        """Only the person the insight concerns dismisses it; the assistant, the engine and every other actor change
        nothing (AGENTS 1 rule 4). The warning dismisses with it, in the same unit of work."""
        w = self.rt.warnings.for_insight(uow.connection, insight_id)
        if w is None or not w.active or actor.kind != "person" or actor.id != w.person_id:
            return
        done = dismiss(w, PersonActor(actor.id), uow.now, None)
        core.commit_final(self.rt, uow, w, done)
