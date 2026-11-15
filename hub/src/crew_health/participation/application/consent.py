"""The owner's crew goal consent (D10): per member, mission and origin, off by default, changed only by the member's
own session. The command is idempotent through E01's ledger (SYSTEM 5.8) and guarded by the revision (If-Match)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from crew_health.kernel.commands import StoredResult, canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.participation.application.context import (
    Deps,
    audit,
    partition_of,
    rules_version,
    today_of,
)
from crew_health.participation.application.errors import problem
from crew_health.participation.domain.crew_goal import set_consent
from crew_health.participation.domain.errors import InvalidConsent, ParticipationError

OPERATION = "setCrewGoalConsent"
TARGET = "/v1/me/crew-goal-consent"


@dataclass(frozen=True, slots=True)
class ConsentState:
    mission_id: str
    origin_mode: str
    enabled: bool
    effective_at: str | None
    revision: int

    def wire(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "origin_mode": self.origin_mode,
            "enabled": self.enabled,
            "effective_at": self.effective_at,
            "revision": self.revision,
        }


def _owner(session: SessionContext) -> str:
    """Only the authenticated owner reads or changes their consent."""
    if session.kind != "crew" or session.actor.kind != "person" or session.person_id is None:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session.person_id


class ConsentCommands:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def get(self, session: SessionContext) -> ConsentState:
        deps, person = self.deps, _owner(session)
        with deps.snapshots.snapshot() as view:
            mission, origin = partition_of(deps, view)
            consent = deps.store.consent(view.connection, person, mission, origin)
            at = deps.store.consent_effective_at(view.connection, person, mission, origin)
        return ConsentState(mission, origin, consent.enabled, at, consent.revision)

    def put(self, session: SessionContext, *, enabled: bool, if_match: int | None, key: str) -> ConsentState:
        deps, person = self.deps, _owner(session)
        if if_match is None:
            raise ProblemError(ErrorCode.VERSION_REQUIRED, ErrorCode.VERSION_REQUIRED.value)
        body = canonical_json({"enabled": enabled, "if_match": if_match})
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
            mission, origin = partition_of(deps, uow)
            if stored is not None:
                # A retry returns the state now in force, as the command's resource (SYSTEM 5.8).
                now = deps.store.consent(uow.connection, person, mission, origin)
                at = deps.store.consent_effective_at(uow.connection, person, mission, origin)
                return ConsentState(mission, origin, now.enabled, at, now.revision)
            current = deps.store.consent(uow.connection, person, mission, origin)
            today, _ = today_of(deps, uow.now)
            try:
                changed = set_consent(current, enabled=enabled, today=today, expected_revision=if_match)
            except InvalidConsent as error:
                raise ProblemError(
                    ErrorCode.STALE_VERSION,
                    ErrorCode.STALE_VERSION.value,
                    current_state={"revision": current.revision},
                ) from error
            except ParticipationError as error:
                raise problem(error) from error
            if changed != current:
                deps.store.append_consent(
                    uow.connection,
                    patient_id=person,
                    mission_id=mission,
                    origin=origin,
                    consent=changed,
                    at=uow.now,
                    policy_version=rules_version(uow),
                )
                ref = deps.ids.derived("pgc", person, mission, origin, str(changed.revision))
                audit(
                    deps,
                    uow,
                    "participation.consent_changed",
                    "consent",
                    "participation.consent",
                    ref,
                    {"enabled": changed.enabled},
                )
            at = deps.store.consent_effective_at(uow.connection, person, mission, origin)
            result = ConsentState(mission, origin, changed.enabled, at, changed.revision)
            receipt = deps.receipts.accepted(uow, identity)
            deps.commands.record(
                uow,
                identity,
                StoredResult(
                    receipt,
                    200,
                    deps.ids.derived("pgc", person, mission, origin, str(changed.revision)),
                    "accepted",
                    None,
                ),
            )
            return result
