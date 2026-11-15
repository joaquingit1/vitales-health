"""ActOnWarning (E24 4.1 UC-R2) and routine overrides (UC-R7) over E01's unit of work and idempotency ledger.

Order (4.1): ledger lookup before any mutable precondition, visibility, If-Match, Config-Release for actions that read
configuration, action allowed, then warning, outbox, audit, ledger and the first result in one commit. Rejections write
nothing; a retry recomputes the same rejection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.commands import CommandIdentity, CommandReceipts, IdempotencyStore, StoredResult
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.uow import SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.rules.application import core
from crew_health.rules.application.core import Runtime, note_audit, replicate_local
from crew_health.rules.application.queries import context_of, project, requester_of, viewer_of
from crew_health.rules.application.wire import emit
from crew_health.rules.domain import actions
from crew_health.rules.domain.errors import ActionNotAllowed, InvalidTransition, NotVisible, RulesDomainError
from crew_health.rules.domain.visibility import can_see
from crew_health.rules.domain.vocabulary import WarningAction, WarningState
from crew_health.rules.domain.warning import Warning, attach_overrides


@dataclass(frozen=True, slots=True)
class Platform:
    uows: UnitOfWorkFactory
    ledger: IdempotencyStore
    receipts: CommandReceipts


@dataclass(frozen=True, slots=True)
class ActBody:
    action: WarningAction
    reason: str | None
    note: str | None


def problem_of(error: RulesDomainError, current: dict[str, object] | None) -> ProblemError:
    try:
        code = ErrorCode(error.code)
    except ValueError:
        return ProblemError(ErrorCode.INTERNAL_ERROR, error.code, retryable=True)
    keep = current if isinstance(error, InvalidTransition) else None
    return ProblemError(code, error.code, current_state=keep)


def validate(rt: Runtime, body: ActBody) -> None:
    """Field failures are 422 INVALID_VALUE with field_errors (4.1 step 2)."""
    errors: list[FieldError] = []
    limit = rt.settings.integer("rules.max_note_chars")
    if body.action in (WarningAction.RESOLVE,) and not body.reason:
        errors.append(FieldError("reason", "required", "a reason is required"))
    if body.action is WarningAction.ADD_NOTE and not body.note:
        errors.append(FieldError("note", "required", "a note is required"))
    if body.note is not None and len(body.note) > limit:
        errors.append(FieldError("note", "too_long", "the note is too long"))
    if errors:
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value, field_errors=errors)


@dataclass(slots=True)
class WarningActions:
    rt: Runtime
    platform: Platform

    def act(
        self,
        session: SessionContext,
        warning_id: str,
        body: ActBody,
        identity: CommandIdentity,
        *,
        if_match: int,
        config_release: str | None,
    ) -> tuple[dict[str, object], int]:
        validate(self.rt, body)
        if session.actor.id != identity.actor_ref:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        with self.platform.uows.begin(
            actor=session.actor, command=identity, correlation_id=identity.key
        ) as uow:
            prior = self.platform.ledger.lookup(uow, identity)
            if prior is not None:
                return self._replay(uow, session, identity, warning_id)
            return self._apply(uow, session, warning_id, body, identity, if_match, config_release)

    def _replay(
        self, uow: UnitOfWork, session: SessionContext, identity: CommandIdentity, warning_id: str
    ) -> tuple[dict[str, object], int]:
        stored = self.rt.results.get(uow.connection, identity.actor_ref, identity.key)
        window = timedelta(hours=self.rt.settings.integer("rules.action_replay_hours"))
        w = self.rt.warnings.get(uow.connection, warning_id)
        visible = w is not None and can_see(w, viewer_of(session)).full
        # The cache holds only the revision (AGENTS 6: no private text): an unchanged warning is re-projected under
        # the current grant; a later revision gets the accepted-command receipt instead.
        if stored is not None and uow.now - stored[2] <= window and w is not None and visible:
            revision = int(json.loads(stored[1])["revision"])
            if w.revision == revision:
                return project(self.rt, uow, self.rt.facts(uow), w, session), revision
        state = None
        if w is not None and visible:
            state = project(self.rt, uow, self.rt.facts(uow), w, session)
        raise ProblemError(
            ErrorCode.DUPLICATE_COMMAND, ErrorCode.DUPLICATE_COMMAND.value, current_state=state
        )

    def _apply(
        self, uow: UnitOfWork, session: SessionContext, warning_id: str, body: ActBody,
        identity: CommandIdentity, if_match: int, config_release: str | None,
    ) -> tuple[dict[str, object], int]:  # fmt: skip
        rt, c = self.rt, uow.connection
        w = rt.warnings.get(c, warning_id)
        if w is None or not can_see(w, viewer_of(session)).full:
            raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
        facts = rt.facts(uow)
        current = project(rt, uow, facts, w, session)
        if if_match != w.revision:
            raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value, current_state=current)
        if body.action is WarningAction.RESOLVE and config_release not in (None, uow.release.release_id):
            raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, ErrorCode.CONFIG_VERSION_MISMATCH.value)
        try:
            done = actions.apply_action(
                w, requester_of(session, w), context_of(rt, uow, facts, w), body.action,
                at=uow.now, reason=body.reason, note=body.note,
            )  # fmt: skip
        except NotVisible as error:
            raise ProblemError(ErrorCode.NOT_FOUND, error.code) from error
        except (ActionNotAllowed, InvalidTransition) as error:
            raise problem_of(error, current) from error
        return self._commit(uow, session, w, done, identity)

    def _commit(
        self, uow: UnitOfWork, session: SessionContext, before: Warning, done: Warning, identity: CommandIdentity
    ) -> tuple[dict[str, object], int]:  # fmt: skip
        rt, c = self.rt, uow.connection
        final = done.state in (WarningState.RESOLVED, WarningState.DISMISSED)
        if final:
            core.commit_final(rt, uow, before, done)
        else:
            if not rt.warnings.update(c, done, expected_revision=before.revision):
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            replicate_local(uow, done.warning_id, "version")
            emit(uow, EventType.WARNING_UPDATED, done)
            note_audit(rt, uow, "warning.updated", {"warning": done.warning_id}, {})
        body = project(rt, uow, rt.facts(uow), done, session)
        reference = json.dumps(
            {"revision": done.revision}, sort_keys=True
        )  # a reference, never the projection
        rt.results.put(c, identity.actor_ref, identity.key, done.warning_id, reference, uow.now)
        receipt = self.platform.receipts.accepted(uow, identity)
        self.platform.ledger.record(
            uow, identity,
            StoredResult(receipt, 200, done.warning_id, "accepted", json.dumps({"revision": done.revision}, separators=(",", ":")).encode()),
        )  # fmt: skip
        return body, done.revision


def attach_routine_overrides(
    rt: Runtime, uow: UnitOfWork, warning_id: str, override_ids: tuple[str, ...]
) -> None:
    """UC-R7: set union; an unknown or final warning is InvalidTransition (the routine owner ends the binding)."""
    w = rt.warnings.get(uow.connection, warning_id)
    if w is None or not w.active:
        raise ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
    union = tuple(sorted(set(w.routine_override_ids) | set(override_ids)))
    if union == w.routine_override_ids:
        return
    done = attach_overrides(w, union)
    if not rt.warnings.update(uow.connection, done, expected_revision=w.revision):
        raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value, retryable=True)
    replicate_local(uow, warning_id, "version")
    emit(uow, EventType.WARNING_UPDATED, done)
    note_audit(rt, uow, "warning.updated", {"warning": warning_id}, {})
