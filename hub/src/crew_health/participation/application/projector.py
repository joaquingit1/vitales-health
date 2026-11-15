"""The projector (E22 4.1, 5.3): turns intents into completions, one intent per transaction, and keeps the award
ledger. It runs after the producer committed (on `settle`, on a read that finds a pending intent, and on rebuild),
never inside the producer's transaction. A fault parks the intent after the bounded retries; it never reaches a
producer."""

from __future__ import annotations

import json
from typing import Literal

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import UnitOfWork
from crew_health.participation.application.context import (
    ENGINE,
    Deps,
    audit,
    partition_of,
    rules_of,
    rules_version,
    today_of,
)
from crew_health.participation.application.ports import Namespace, View
from crew_health.participation.application.store import IntentRow
from crew_health.participation.application.wire import basis_from_fields
from crew_health.participation.domain.derivation import (
    AwardEvent,
    eligible_days,
    next_award_events,
    reached_attributions,
    resolve_first_day,
)
from crew_health.participation.domain.errors import ParticipationError
from crew_health.participation.domain.identity import award_id
from crew_health.participation.domain.projection import ProjectionDecision, build_completion, decide
from crew_health.participation.domain.types import CompletionBasis, ViewOrigin


def _decision(basis: CompletionBasis, record_origin: Namespace | None) -> ProjectionDecision:
    """3.4. A person with no record origin, or one on a REPLAY roster, cannot match a LIVE or SIM basis."""
    if basis.origin_mode == "REPLAY":
        return ProjectionDecision("nothing")
    if record_origin is None or record_origin == "REPLAY":
        return ProjectionDecision("rejected", "ORIGIN_MISMATCH")
    return decide(basis, record_origin=record_origin)


class Projector:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def drain(self) -> int:
        """Projects every pending intent; returns how many projection attempts ran. Bounded: each intent is
        attempted at most `projection_attempts` times, then parked."""
        deps, ran = self.deps, 0
        for _ in range(deps.limits.projection_attempts + 1):
            with deps.snapshots.snapshot() as view:
                batch = deps.store.pending(view.connection, deps.limits.projection_batch)
            if not batch:
                break
            for row in batch:
                self._project(row)
                ran += 1
        return ran

    def settle(self, intent_key: str) -> Literal["settled", "pending", "parked"]:
        self.drain()
        with self.deps.snapshots.snapshot() as view:
            state = self.deps.store.intent_state(view.connection, intent_key)
        if state == "parked":
            return "parked"
        return "pending" if state in (None, "pending") else "settled"

    def requeue_parked(self) -> int:
        deps = self.deps
        with deps.uows.begin(actor=ENGINE, correlation_id=deps.ids.new("correlation")) as uow:
            return deps.store.requeue_parked(uow.connection, uow.now)

    def _project(self, row: IntentRow) -> None:
        deps = self.deps
        try:
            with deps.uows.begin(actor=ENGINE, correlation_id=deps.ids.new("correlation")) as uow:
                self._apply(uow, row)
        except (ParticipationError, ValueError, KeyError, ProblemError) as error:
            self._fail(row, error)

    def _fail(self, row: IntentRow, error: Exception) -> None:
        deps = self.deps
        attempts = row.attempts + 1
        code = type(error).__name__
        parked = attempts >= deps.limits.projection_attempts
        with deps.uows.begin(actor=ENGINE, correlation_id=deps.ids.new("correlation")) as uow:
            deps.store.mark(
                uow.connection, row.seq, "parked" if parked else "pending", attempts, code, uow.now
            )
            if parked:
                audit(deps, uow, "participation.intent_parked", "intent", "participation.intent", row.key)

    def _apply(self, uow: UnitOfWork, row: IntentRow) -> None:
        deps = self.deps
        parsed: object = json.loads(row.payload)
        if not isinstance(parsed, dict):
            raise ValueError("stored intent is not an object")
        basis = basis_from_fields(parsed)
        origin = deps.origins.record_origin(uow, basis.patient_id)
        record = build_completion(
            basis,
            _decision(basis, origin),
            key=row.key,
            clock=deps.clock,
            rule_version=rules_version(uow),
        )
        if record is None:
            deps.store.mark(uow.connection, row.seq, "nothing", row.attempts + 1, None, uow.now)
            return
        deps.store.insert_completion(uow.connection, record, row.seq)
        deps.store.mark(uow.connection, row.seq, "applied", row.attempts + 1, None, uow.now)
        if record.status == "accepted" and basis.origin_mode != "REPLAY":
            self.sync_awards(uow, basis.patient_id, basis.mission_id, basis.origin_mode)

    def _events(
        self, view: View, person: str, mission: str, origin: ViewOrigin
    ) -> tuple[tuple[AwardEvent, ...], int]:
        """The ledger entries the rules owe this person now, and the ledger length they extend."""
        deps = self.deps
        rules = rules_of(view)
        today, _ = today_of(deps, view.now)
        facts = deps.store.facts(
            view.connection,
            mission_id=mission,
            origin=origin,
            patient_id=person,
            limit=deps.limits.max_completion_days,
        )
        first = resolve_first_day(deps.roster.first_mission_day(view, person), facts, today)
        ledger = deps.store.ledger(view.connection, person, mission, origin)
        events = next_award_events(
            ledger,
            total=len(eligible_days(facts, first, today)),
            reached=reached_attributions(facts, first, today, rules.patch_thresholds),
            rules=rules,
            void_applied=False,
        )
        return events, len(ledger)

    def sync_awards(self, uow: UnitOfWork, person: str, mission: str, origin: ViewOrigin) -> None:
        deps = self.deps
        events, _ = self._events(uow, person, mission, origin)
        for event in events:
            ident = award_id(person, mission, origin, event.threshold)
            deps.store.append_award(
                uow.connection,
                award_id=ident,
                patient_id=person,
                mission_id=mission,
                origin=origin,
                event=event,
                at=uow.now,
            )
            audit(deps, uow, "participation.award_" + event.kind, "award", "participation.award", ident)

    def refresh_awards(self, person: str) -> None:
        """A rules edit or a clock move can owe a person an award no projection has recorded yet; record it."""
        deps = self.deps
        with deps.snapshots.snapshot() as view:
            mission, origin = partition_of(deps, view)
            owed, _ = self._events(view, person, mission, origin)
        if owed:
            with deps.uows.begin(actor=ENGINE, correlation_id=deps.ids.new("correlation")) as uow:
                self.sync_awards(uow, person, mission, origin)
