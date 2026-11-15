"""Session projections that are not on the wire: hold and attention (E24 3.12) and replicas of other nodes (3.15).

R-E24-1 (the `held` and `attention` fields and `held_summary`) and R-E24-2 (`replica_from`) are requests, not
decisions: the contract `Warning` has no such fields and refuses extra ones. These readers therefore compute the
projections for the hub's own callers (E03 Today, E26 help screens, tests) and nothing serves them over HTTP. With
no overlay provider nothing is held: more interruption, never less (3.12 "Exposure").
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, SessionContext
from crew_health.mission.application.ports import OverlayFact as MissionOverlay
from crew_health.mission.application.ports import OverlayPort
from crew_health.rules.application import codec
from crew_health.rules.application.core import Runtime
from crew_health.rules.application.queries import viewer_of
from crew_health.rules.domain import attention, hold
from crew_health.rules.domain.views import Attention, HoldState, ReplicaInfo
from crew_health.rules.domain.visibility import Viewer, can_see
from crew_health.rules.domain.warning import Warning


@dataclass(frozen=True, slots=True)
class HeldDigest:
    """The warnings held for one person in one span, presented once after it (3.12), oldest first."""

    since: datetime
    until: datetime | None
    warning_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionPresentation:
    held: Mapping[str, HoldState]
    attention: Mapping[str, Attention]
    banner: tuple[str, ...]
    emergencies: tuple[str, ...]
    more: int
    digests: tuple[HeldDigest, ...]


@dataclass(frozen=True, slots=True)
class ReplicaView:
    warning_id: str
    origin_node_id: str
    age_seconds: int
    info: ReplicaInfo
    warning: dict[str, object]


def _overlay(fact: MissionOverlay) -> hold.OverlayFact | None:
    start = fact.started_at or fact.planned_start
    return None if start is None else hold.OverlayFact(fact.kind, start, fact.ended_at)


class WarningPresentation(Protocol):
    """For E03 Today and E26 once R-E24-1 and R-E24-2 are decided; today only the hub's own callers use it."""

    def for_session(self, session: SessionContext) -> SessionPresentation: ...
    def replicas(self, session: SessionContext) -> tuple[ReplicaView, ...]: ...


@dataclass(slots=True)
class Presenter:
    rt: Runtime
    snapshots: ReadSnapshotFactory
    overlays: OverlayPort | None

    def for_session(self, session: SessionContext) -> SessionPresentation:
        with self.snapshots.snapshot() as snap:
            viewer = viewer_of(session)
            facts = self.rt.facts_for_snapshot(snap)
            active = self.rt.warnings.active(snap.connection, limit=self.rt.cap("rules.active_scan_max"))
            visible = [w for w in active if can_see(w, viewer).full]
            spans = self._spans(snap, session, facts.suppressed_overlays, visible)
            held = {
                w.warning_id: state
                for w in visible
                if (state := hold.held_state(w.level, w.raised_at, spans, person_session=_person(viewer)))
                is not None
            }
            plan = attention.plan(
                visible,
                person_id=session.person_id,
                held_ids=set(held),
                max_banner_items=self.rt.settings.integer("rules.max_banner_items"),
            )
            digests = self._digests(visible, spans, session.person_id)
            return SessionPresentation(
                held, plan.per_warning, plan.banner, plan.emergencies, plan.more, digests
            )

    def _spans(
        self, snap: ReadSnapshot, session: SessionContext, suppressed: frozenset[str], visible: list[Warning]
    ) -> tuple[hold.HoldSpan, ...]:
        person = session.person_id
        if self.overlays is None or not visible or not _person(viewer_of(session)) or person is None:
            return ()
        since = min(w.raised_at for w in visible)
        try:
            found = self.overlays.intervals(
                person, sorted(suppressed), since, snap.now, self.rt.settings.integer("rules.page_limit")
            )
        except ProblemError as error:
            if error.code is ErrorCode.PAYLOAD_TOO_LARGE:
                return ()  # more overlays than the bound: hold nothing, never a truncated list as complete
            raise
        return hold.spans([o for f in found if (o := _overlay(f)) is not None], suppressed)

    @staticmethod
    def _digests(
        visible: list[Warning], spans: tuple[hold.HoldSpan, ...], person_id: str | None
    ) -> tuple[HeldDigest, ...]:
        if person_id is None:
            return ()
        found = []
        for span in spans:
            items = hold.held_items(visible, span, person_id=person_id)
            if items:
                found.append(HeldDigest(span.start, span.end, tuple(w.warning_id for w in items)))
        return tuple(found)

    def replicas(self, session: SessionContext) -> tuple[ReplicaView, ...]:
        """Warnings of other nodes this session may see, with origin and age (3.15). The engine never reads them."""
        viewer = viewer_of(session)
        found: list[ReplicaView] = []
        with self.snapshots.snapshot() as snap:
            rows = self.rt.replicas.page(snap.connection, limit=self.rt.settings.integer("rules.page_limit"))
            for warning_id, origin_node_id, origin_at, person_id, body in rows:
                if not _replica_visible(viewer, person_id):
                    continue
                age = max(0, int((snap.now - codec.parse_instant(origin_at)).total_seconds()))
                found.append(
                    ReplicaView(
                        warning_id, origin_node_id, age, ReplicaInfo(origin_node_id, age), _object(body)
                    )
                )
        return tuple(found)


def _object(text: str) -> dict[str, object]:
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def _person(viewer: Viewer) -> bool:
    return viewer.kind == "crew" and viewer.person_id is not None


def _replica_visible(viewer: Viewer, person_id: str | None) -> bool:
    if person_id is None:
        return True
    return _person(viewer) and viewer.person_id == person_id


__all__ = ["HeldDigest", "Presenter", "ReplicaView", "SessionPresentation", "WarningPresentation"]
