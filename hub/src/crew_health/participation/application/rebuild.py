"""rebuildParticipation (E22 4.1): re-projects parked intents and recomputes every view, returning counts and a hash,
never a per person view. Deterministic: the same completions, roster, clock history and rules give the same hash."""

from __future__ import annotations

import hashlib

from crew_health.kernel.commands import StoredResult, canonical_json, command_identity
from crew_health.kernel.uow import SessionContext
from crew_health.participation.application.context import Deps, partition_of
from crew_health.participation.application.projector import Projector
from crew_health.participation.application.queries import Queries
from crew_health.participation.application.wire import wire_instant
from crew_health.participation.domain.canonical import result_sha256
from crew_health.participation.domain.derivation import award_views
from crew_health.participation.domain.types import ViewOrigin

OPERATION = "rebuildParticipation"
TARGET = "/v1/participation/rebuild"


class Rebuild:
    def __init__(self, deps: Deps, projector: Projector, queries: Queries) -> None:
        self.deps, self.projector, self.queries = deps, projector, queries

    def run(self, session: SessionContext, *, origin_mode: ViewOrigin, key: str) -> dict[str, object]:
        deps = self.deps
        body = canonical_json({"origin_mode": origin_mode})
        identity = command_identity(
            key=key,
            actor_ref=session.actor.kind + ":" + session.actor.id,
            operation=OPERATION,
            target=TARGET,
            parameters_json=body,
            body_fingerprint=hashlib.sha256(body).hexdigest(),
            fingerprint_kind="sha256",
        )
        with deps.uows.begin(
            actor=session.actor, command=identity, correlation_id=deps.ids.new("correlation")
        ) as uow:
            stored = deps.commands.lookup(uow, identity)
        replay = stored is not None
        self.projector.requeue_parked()
        self.projector.drain()
        result = self._compute(origin_mode)
        with deps.uows.begin(
            actor=session.actor, command=identity, correlation_id=deps.ids.new("correlation")
        ) as uow:
            result["finished_at"] = wire_instant(uow.now)
            if deps.commands.lookup(uow, identity) is None and not replay:
                receipt = deps.receipts.accepted(uow, identity)
                deps.commands.record(
                    uow,
                    identity,
                    StoredResult(receipt, 200, deps.ids.derived("prb", origin_mode, key), "accepted", None),
                )
        return result

    def _compute(self, origin_mode: ViewOrigin) -> dict[str, object]:
        deps = self.deps
        with deps.snapshots.snapshot() as view:
            mission, _ = partition_of(deps, view)
            rows = view.connection.rows(
                "SELECT DISTINCT patient_id FROM participation_completion"
                " WHERE mission_id = ? AND origin_mode = ? AND status = 'accepted' ORDER BY patient_id",
                (mission, origin_mode),
                limit=deps.limits.roster_max + 1,
            )
            people = [str(r[0]) for r in rows]
            read = view.connection.rows(
                "SELECT COUNT(*) FROM participation_completion WHERE mission_id = ? AND origin_mode = ?",
                (mission, origin_mode),
                limit=1,
            )
            streaks = [self.queries._streak(view, person, mission, origin_mode) for person in people]  # noqa: SLF001
            awards = [
                a
                for person in people
                for a in award_views(
                    person,
                    mission,
                    origin_mode,
                    deps.store.ledger(view.connection, person, mission, origin_mode),
                )
            ]
        goal = self.queries.derive_crew_goal().view
        return {
            "origin_mode": origin_mode,
            "events_read": int(str(read[0][0])),
            "events_voided": 0,
            "views_rebuilt": len(streaks),
            "changed_day_assignments": 0,
            "result_sha256": result_sha256(
                streaks, awards, goal if goal.origin_mode == origin_mode else None
            ),
        }
