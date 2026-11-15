"""Read side (E24 3.10, 3.16, 4.2): what a session may see of each warning, served steps and allowed actions.

Authorization comes only from the session. An invisible warning is NOT_FOUND. Steps pass the guidance rule on every
read: LIVE steps are served only when an eligibility verdict says show; with no verdict provider for this content kind
(R-E24-23) a LIVE warning serves no steps and the unsupported state, never the stored text (INV-19).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.rules.application.core import Runtime
from crew_health.rules.application.facts import ReleaseFacts
from crew_health.rules.application.ports import WarningFact
from crew_health.rules.application.wire import fact_of, wire_json
from crew_health.rules.domain import actions, guidance, ordering
from crew_health.rules.domain.activation import definition_status
from crew_health.rules.domain.errors import NotVisible
from crew_health.rules.domain.visibility import Viewer, can_see
from crew_health.rules.domain.vocabulary import ACTIVE, EnvironmentId, Level, OriginMode, WarningAction
from crew_health.rules.domain.warning import Actor, AssistantActor, PersonActor, SessionActor, Warning

type View = ReadSnapshot | UnitOfWork


def viewer_of(session: SessionContext) -> Viewer:
    return Viewer(session.kind, session.person_id)


def actor_of(session: SessionContext) -> Actor:
    """The domain actor of a session. An assistant actor stays the assistant even inside a crew member's session,
    so no transition accepts it (AGENTS 1 rule 4): the session kind never launders the assistant into a person."""
    if session.actor.kind == "assistant":
        return AssistantActor(session.actor.id)
    if session.kind == "crew" and session.person_id is not None and session.actor.kind == "person":
        return PersonActor(session.person_id)
    return SessionActor(session.kind, session.actor.id)


def authority_of(session: SessionContext, actor: Actor) -> actions.Authority:
    """D21: the crew medical officer resolves a warning whose definition was edited or withdrawn. The role comes
    from the authenticated session's grants (E02), only for a person acting in their own crew session; the
    assistant and every other session kind hold no authority."""
    if isinstance(actor, PersonActor) and Role.CREW_MEDICAL_OFFICER in session.roles:
        return "crew_medical_officer"
    return "none"


def requester_of(session: SessionContext, w: Warning) -> actions.Requester:
    actor = actor_of(session)
    return actions.Requester(
        viewer_of(session), actor, authority_of(session, actor), None, isinstance(actor, PersonActor)
    )


def served(w: Warning) -> guidance.ServedSteps:
    """Interim of 3.16: no eligibility provider exists for `warning_steps`, so only SIM and REPLAY serve steps."""
    verdict = (
        None
        if w.origin_mode is OriginMode.LIVE
        else guidance.GuidanceVerdict("show", "simulation_only", "not_clinically_reviewed")
    )
    return guidance.serve(w, verdict)


def context_of(rt: Runtime, view: View, facts: ReleaseFacts, w: Warning) -> actions.ActionContext:
    mission = None if rt.mission is None else rt.mission.current(view)
    env = EnvironmentId(mission.active_environment_id) if mission is not None else w.environment_id
    status = definition_status(
        w, facts.definitions, facts.fingerprints, facts.content_hashes, environment_id=env
    )
    return actions.ActionContext(
        rt.node_id(view),
        status, rt.settings.integer("rules.max_notes_per_warning"), False,
    )  # fmt: skip


def project(
    rt: Runtime, view: View, facts: ReleaseFacts, w: Warning, session: SessionContext
) -> dict[str, object]:
    """The wire warning for this session; raises NotVisible for a person warning of someone else."""
    if not can_see(w, viewer_of(session)).full:
        raise NotVisible("not found")
    allowed = actions.allowed_actions(w, requester_of(session, w), context_of(rt, view, facts, w))
    return wire_json(w, steps=served(w).steps, allowed=allowed)


def not_found() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)


@dataclass(slots=True)
class WarningReader:
    rt: Runtime
    snapshots: ReadSnapshotFactory

    def get(self, session: SessionContext, warning_id: str) -> tuple[dict[str, object], int]:
        with self.snapshots.snapshot() as snap:
            w = self.rt.warnings.get(snap.connection, warning_id)
            if w is None or not can_see(w, viewer_of(session)).full:
                raise not_found()
            return project(self.rt, snap, self.rt.facts_for_snapshot(snap), w, session), w.revision

    def listing(
        self, session: SessionContext, *, state: str, after: tuple[int, str, str] | None, limit: int
    ) -> tuple[list[dict[str, object]], tuple[int, str, str] | None]:
        """One page of the session's warnings and the cursor of the last row consumed, or None at the end. A full
        page followed by a further row returns a cursor; a page that ends the list returns None."""
        with self.snapshots.snapshot() as snap:
            facts = self.rt.facts_for_snapshot(snap)
            items: list[dict[str, object]] = []
            consumed = after
            seen = 0
            while seen < 10 * limit:
                batch = self.rt.warnings.page(snap.connection, state=state, after=consumed, limit=limit)
                for w in batch:
                    if len(items) == limit:
                        return (
                            items,
                            consumed,
                        )  # a visible row may follow: the next page starts after `consumed`
                    consumed = (w.headline.rank, w.raised_at.isoformat().replace("+00:00", "Z"), w.warning_id)
                    seen += 1
                    if can_see(w, viewer_of(session)).full:
                        items.append(project(self.rt, snap, facts, w, session))
                if len(batch) < limit:
                    return items, None
            return items, consumed

    def visible(self, session: SessionContext, warning_id: str) -> bool:
        with self.snapshots.snapshot() as snap:
            w = self.rt.warnings.get(snap.connection, warning_id)
            return w is not None and can_see(w, viewer_of(session)).full

    def count_active_visible(self, session: SessionContext) -> int:
        with self.snapshots.snapshot() as snap:
            active = self.rt.warnings.active(snap.connection, limit=self.rt.cap("rules.active_scan_max"))
            return sum(1 for w in active if can_see(w, viewer_of(session)).full)

    def active_shared_at_or_above(self, level: str) -> Sequence[WarningFact]:
        wanted = Level(level)
        with self.snapshots.snapshot() as snap:
            active = self.rt.warnings.active(snap.connection, limit=self.rt.cap("rules.active_scan_max"))
            shared = [
                w for w in active if w.scope.value != "person" and w.level >= wanted and w.state in ACTIVE
            ]
            return tuple(fact_of(w) for w in ordering.ordered(shared))


__all__ = ["WarningReader", "WarningAction"]
