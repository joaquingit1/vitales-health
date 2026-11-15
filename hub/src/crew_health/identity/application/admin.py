"""E02 4 owner and operator commands: roster document, credential set or change, adapter tokens.

Each is one bounded unit of work. A credential verifier is computed outside the writer and the principal
generation is rechecked inside it, so an interrupted or raced change can never mint an old-generation token.
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.identity.application.service import IdentityService, SessionView
from crew_health.identity.application.store import RosterState
from crew_health.identity.application.support import (
    IdentityDeps,
    audit,
    declaration,
    reference,
    settable_credential,
)
from crew_health.identity.domain.roster import RosterBootstrap, invalid, validate_bootstrap
from crew_health.identity.domain.session import actor_for, roles_for
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor


@dataclass(frozen=True, slots=True)
class RosterResult:
    revision: int
    removed: tuple[str, ...]


class IdentityAdmin:
    def __init__(self, deps: IdentityDeps, sessions: IdentityService) -> None:
        self.deps, self.sessions = deps, sessions

    def apply_roster(
        self, document: RosterBootstrap, *, expected_revision: int, operator: Actor, correlation_id: str
    ) -> RosterResult:
        """The whole roster as one document: members absent from it are removed and their sessions end."""
        deps = self.deps
        if operator.kind != "operator":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        validate_bootstrap(
            document, roster_max=deps.settings.roster_max, day_span_max=deps.settings.day_span_max
        )
        with deps.uows.begin(actor=operator, correlation_id=correlation_id) as uow:
            connection = uow.connection
            state = deps.store.roster_state(connection)
            revision = 0 if state is None else state.revision
            if revision != expected_revision:
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            if state is not None and (
                state.mission_id != document.mission_id or state.namespace != document.namespace
            ):
                raise invalid("mission_id")
            removed = deps.store.apply_roster(
                connection, document, removed_at=uow.now, limit=deps.settings.roster_max * 2 + 16
            )
            for principal_id in removed:
                self.sessions.end_principal_sessions(uow, principal_id, "member_removed")
            deps.store.save_roster_state(
                connection, RosterState(document.mission_id, document.namespace, revision + 1)
            )
            deps.store.bump_access_generation(connection)
            roster_id = deps.ids.derived("roster", document.mission_id)
            reference(uow, record_type="identity.roster", record_id=roster_id, operation="version")
            audit(uow, deps.ids, declaration("identity.roster_applied"), roster_id, {})
        return RosterResult(revision + 1, removed)

    def set_credential(
        self,
        principal_id: str,
        new_credential: str,
        *,
        current_credential: str | None,
        operator: Actor,
        client: str,
        correlation_id: str,
    ) -> int:
        """First provisioning by an operator, or a change proven by the current credential.

        Operator reset of an existing credential stays disabled (E02 5, R-E02-16): it would need the
        shared protocol that locks prior private unwrap in the same transaction.
        """
        deps = self.deps
        secret = settable_credential(new_credential, max_bytes=deps.settings.credential_max_bytes)
        with deps.snapshots.snapshot() as view:
            principal = deps.store.principal(view.connection, principal_id)
        if principal is None or not principal.enabled or principal.kind == "adapter":
            raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
        if principal.verifier is None:
            if operator.kind != "operator" or current_credential is not None:
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        elif current_credential is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        else:
            self.sessions.prove(principal, current_credential, client=client, correlation_id=correlation_id)
        verifier = deps.hasher.hash(secret)
        actor = operator if principal.verifier is None else actor_for(principal.kind, principal_id)
        with deps.uows.begin(actor=actor, correlation_id=correlation_id) as uow:
            latest = deps.store.principal(uow.connection, principal_id)
            if (
                latest is None
                or latest.generation != principal.generation
                or latest.verifier != principal.verifier
            ):
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            generation = principal.generation + 1
            deps.store.set_verifier(uow.connection, principal_id, verifier, generation)
            self.sessions.end_principal_sessions(uow, principal_id, "credential_changed")
            deps.store.bump_access_generation(uow.connection)
            reference(uow, record_type="identity.principal", record_id=principal_id, operation="version")
            audit(
                uow, deps.ids, declaration("identity.credential_set"), principal_id, {"kind": principal.kind}
            )
        return generation

    def issue_adapter_token(
        self, principal_id: str, *, listener: str, operator: Actor, correlation_id: str
    ) -> SessionView:
        """Explicit in-process provisioning of an adapter bearer token, returned once (E02 3)."""
        deps = self.deps
        if operator.kind != "operator":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        with deps.uows.begin(actor=operator, correlation_id=correlation_id) as uow:
            principal = deps.store.principal(uow.connection, principal_id)
            if principal is None or principal.kind != "adapter" or not principal.enabled:
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            record, token = self.sessions.start(uow, principal, listener=listener)
            return SessionView(record, roles_for("adapter", None), principal.display_name, None, token)
