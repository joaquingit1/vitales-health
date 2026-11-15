"""Read side (E24 3.10, 3.16, 4.2): what a session may see of each warning, served steps and allowed actions.

Authorization comes only from the session. An invisible warning is NOT_FOUND. Steps pass the guidance rule on every
read: LIVE steps are served only when an eligibility verdict says show; with no verdict provider for this content kind
(R-E24-23) a LIVE warning serves no steps and the unsupported state, never the stored text (INV-19).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
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
from crew_health.rules.domain.warning import PersonActor, SessionActor, Warning

type View = ReadSnapshot | UnitOfWork


def viewer_of(session: SessionContext) -> Viewer:
    return Viewer(session.kind, session.person_id)


def requester_of(session: SessionContext, w: Warning) -> actions.Requester:
    viewer = viewer_of(session)
    actor = (
        PersonActor(session.person_id)
        if session.kind == "crew" and session.person_id is not None
        else SessionActor(session.kind, session.actor.id)
    )
    return actions.Requester(viewer, actor, "none", None, session.kind == "crew")


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
        with self.snapshots.snapshot() as snap:
            facts = self.rt.facts_for_snapshot(snap)
            items: list[dict[str, object]] = []
            last: tuple[int, str, str] | None = after
            seen = 0
            while len(items) < limit and seen < 10 * limit:
                batch = self.rt.warnings.page(snap.connection, state=state, after=last, limit=limit)
                if not batch:
                    return items, None
                for w in batch:
                    last = (w.headline.rank, w.raised_at.isoformat().replace("+00:00", "Z"), w.warning_id)
                    seen += 1
                    if can_see(w, viewer_of(session)).full and len(items) < limit:
                        items.append(project(self.rt, snap, facts, w, session))
                if len(batch) < limit:
                    return items, None
            return items, last

    def visible(self, session: SessionContext, warning_id: str) -> bool:
        with self.snapshots.snapshot() as snap:
            w = self.rt.warnings.get(snap.connection, warning_id)
            return w is not None and can_see(w, viewer_of(session)).full

    def count_active_visible(self, session: SessionContext) -> int:
        with self.snapshots.snapshot() as snap:
            active = self.rt.warnings.active(
                snap.connection, limit=self.rt.settings.integer("rules.active_scan_max")
            )
            return sum(1 for w in active if can_see(w, viewer_of(session)).full)

    def active_shared_at_or_above(self, level: str) -> Sequence[WarningFact]:
        wanted = Level(level)
        with self.snapshots.snapshot() as snap:
            active = self.rt.warnings.active(
                snap.connection, limit=self.rt.settings.integer("rules.active_scan_max")
            )
            shared = [
                w for w in active if w.scope.value != "person" and w.level >= wanted and w.state in ACTIVE
            ]
            return tuple(fact_of(w) for w in ordering.ordered(shared))


__all__ = ["WarningReader", "WarningAction"]
