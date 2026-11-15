"""Subject-only plan run creation and reads using the durable command ledger."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from crew_health.contracts_gen.schemas.requests_schema import PlanRunCreate
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.plans.application.ground_log import GroundLine
from crew_health.plans.application.pinning import opening_context
from crew_health.plans.application.release import pins, return_option_present
from crew_health.plans.application.world import PlansWorld, View, authorize, fail, human
from crew_health.plans.domain.model import canonical_json as plan_json
from crew_health.plans.domain.run import PlanRun, RunEffects, open_run


def identity_for(
    session: SessionContext, key: str, operation: str, target: str, body: object
) -> CommandIdentity:
    return command_identity(
        key=key,
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=b"{}",
        body_fingerprint=hashlib.sha256(canonical_json(body)).hexdigest(),
        fingerprint_kind="sha256",
    )


def result_for(
    world: PlansWorld, uow: UnitOfWork, identity: CommandIdentity, run_id: str, status: int
) -> StoredResult:
    return StoredResult(
        AcceptedReceipt(identity.key, identity.operation, identity.target, world.ids.new("rcp"), uow.now),
        status,
        run_id,
        "accepted",
        None,
    )


def replay(world: PlansWorld, view: View, session: SessionContext, result: StoredResult) -> dict[str, object]:
    if result.resource_ref is None:
        raise fail(ErrorCode.INTERNAL_ERROR)
    run = world.load(view, result.resource_ref)
    authorize(session, run.subject_person_id)
    return world.wire(view, run, session)


class PlanRuns:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def start(
        self,
        session: SessionContext,
        body: Mapping[str, object] | PlanRunCreate,
        key: str,
        config_release: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        human(session)
        request = body if isinstance(body, PlanRunCreate) else PlanRunCreate.model_validate(body)
        authorize(session, request.subject_person_id.root)
        identity = identity_for(session, key, "startPlanRun", "plan-runs", request.model_dump(mode="json"))
        with self.world.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.world.ids.new("cor")
        ) as uow:
            stored = self.world.idempotency.lookup(uow, identity)
            if stored is not None:
                return 201, replay(self.world, uow, session, stored)
            if config_release is not None and config_release != uow.release.release_id:
                raise fail(ErrorCode.CONFIG_VERSION_MISMATCH)
            run, version = self._open(uow, session, request)
            self.world.repository.insert(uow, run, version)
            self.world.persist(uow, None, run, RunEffects((), "plan_run.changed", "opened"))
            self.world.idempotency.record(
                uow, identity, result_for(self.world, uow, identity, run.plan_run_id, 201)
            )
            return 201, self.world.wire(uow, run, session)

    def _open(self, uow: UnitOfWork, session: SessionContext, body: PlanRunCreate) -> tuple[PlanRun, str]:
        if (body.episode_id is None) == (body.help_session_id is None):
            raise fail(ErrorCode.INVALID_VALUE, "/episode_id")
        document = self.world.catalog.document(uow.release)
        plan = None if document is None else document.plan(body.plan_id)
        if document is None or plan is None:
            raise fail(ErrorCode.INVALID_VALUE, "/plan_id")
        mission = self.world.current_mission(uow)
        if mission is None or mission.active_environment_id not in plan.environments:
            raise fail(ErrorCode.CONTEXT_INAPPLICABLE)
        self._subject(session, body)
        reference = (
            body.episode_id.root
            if body.episode_id is not None
            else (None if body.help_session_id is None else body.help_session_id.root)
        )
        if reference is None:
            raise fail(ErrorCode.INVALID_VALUE, "/episode_id")
        duplicate = self.world.repository.open_for(uow, body.plan_id, body.subject_person_id.root, reference)
        if duplicate is not None:
            raise ProblemError(
                ErrorCode.INVALID_VALUE,
                "open_run_exists",
                current_state=self.world.wire(uow, self.world.load(uow, duplicate), session),
            )
        raw = self.world.catalog.raw_plan(uow.release, plan.plan_id)
        if raw is None:
            raise fail(ErrorCode.INVALID_VALUE, "/plan_id")
        self.world.repository.pin_plan(uow, document.document_sha256, plan.plan_id, plan_json(raw))
        # The ground-line boundary supplies this audit pin; no rule reads it.
        run = open_run(
            plan=plan,
            environment_id=mission.active_environment_id,
            plan_run_id=self.world.ids.new("prun"),
            pins=pins(uow.release),
            opened_release_id=uow.release.release_id,
            subject_person_id=body.subject_person_id.root,
            episode_id=None if body.episode_id is None else body.episode_id.root,
            help_session_id=None if body.help_session_id is None else body.help_session_id.root,
            origin_mode=mission.mission_mode,
            opened_by=session.actor,
            at=uow.now,
            link=(self.world.ground_line or GroundLine(None, None)).at_open(),
            return_option_present=return_option_present(uow.release, mission.active_environment_id),
        )
        context_json = opening_context(uow.release, plan, raw)
        if len(context_json.encode("utf-8")) > self.world.settings.max_document_bytes:
            raise fail(ErrorCode.INVALID_VALUE, "/plan_id")
        self.world.repository.pin_context(uow, run.plan_run_id, context_json)
        return run, document.document_sha256

    def _subject(self, session: SessionContext, body: PlanRunCreate) -> None:
        if body.episode_id is not None:
            episode = (
                None
                if self.world.episodes is None
                else self.world.episodes.visible(session, body.episode_id.root)
            )
            if episode is None or episode.subject_person_id != body.subject_person_id.root:
                raise fail(ErrorCode.INVALID_VALUE, "/episode_id")
        elif body.help_session_id is not None:
            help_session = (
                None
                if self.world.help_sessions is None
                else self.world.help_sessions.snapshot(body.help_session_id.root)
            )
            if (
                help_session is None
                or not help_session.open
                or help_session.subject_person_id != body.subject_person_id.root
            ):
                raise fail(ErrorCode.INVALID_VALUE, "/help_session_id")

    def get(self, session: SessionContext, plan_run_id: str) -> dict[str, object]:
        if session.kind == "ground":
            raise fail(ErrorCode.CONSENT_REQUIRED)
        # D9 denies station detail access before a lookup, including absent run IDs.
        if session.kind != "crew" or session.person_id is None:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        with self.world.snapshots.snapshot() as view:
            run = self.world.load(view, plan_run_id)
            authorize(session, run.subject_person_id)
            return self.world.wire(view, run, session)

    def list(
        self,
        session: SessionContext,
        subject: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        if session.kind != "crew" or session.person_id is None:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        person = session.person_id if subject is None else subject
        authorize(session, person)
        size = self.world.settings.page_limit_max if limit is None else limit
        if size < 1 or size > self.world.settings.page_limit_max:
            raise fail(ErrorCode.INVALID_VALUE, "/limit")
        with self.world.snapshots.snapshot() as view:
            runs = self.world.repository.list_open(view, person, cursor, size)
            return {
                "items": [self.world.wire(view, run, session) for run in runs],
                "page": {"next_cursor": runs[-1].plan_run_id if len(runs) == size else None},
            }
