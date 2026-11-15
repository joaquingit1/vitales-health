"""openConversation, getConversation and sendMessage with a bounded turn worker (E13 3.1, 3.2, 3.5, 5).

A send is accepted at once (202 with the person's message); one worker thread runs queued turns, one at a time, through
the controller and commits the outcome. Every stop is stored as what it is: a timed out, failed, rejected or cancelled
notice with its error code. The model being stopped refuses the send with 503 and stores nothing, and nothing in
Someone needs help, warnings or records waits on this module (AGENTS rule 3).
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast

from crew_health.assistant.adapters.store import Store
from crew_health.assistant.adapters.turn_boundary import TurnBoundary, TurnFacts, kit_limitations
from crew_health.assistant.application.content import Policy
from crew_health.assistant.application.controller import Completed, Controller, Plan, Stopped
from crew_health.assistant.application.dto import Conversation, Work
from crew_health.assistant.application.http_ports import CommandRequest, HttpResult
from crew_health.assistant.application.json_data import JsonBounds
from crew_health.assistant.application.runtime_actions import RuntimeActions
from crew_health.assistant.application.tools_copies import (
    ContextBudget,
    EffectApplier,
    OutputPolicyPort,
    Phase,
    ToolGateway,
    ToolScope,
)
from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.turn import TurnState
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.clock import Clock, TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.model.application.ports import (
    InferencePort,
    ModelReleaseReader,
    PromptPort,
    StepProgress,
    TranscriptItem,
)


class EpisodeAccess(Protocol):
    """E15 EpisodeFacts.visible_to, bound by the composer."""

    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool: ...


class TurnAnchor(Protocol):
    """A capture of the hub clock on this process's hub segment (SYSTEM 5.4), bound by the composer."""

    def anchor(self, origin_mode: Literal["LIVE", "REPLAY", "SIM"]) -> TimeInput | None: ...


class PersonOrigin(Protocol):
    """The roster's mission and origin namespace (E02 partition), bound by the composer."""

    def partition(self) -> tuple[str, Literal["LIVE", "REPLAY", "SIM"]] | None: ...


@dataclass(frozen=True, slots=True)
class TurnPorts:
    gateway: ToolGateway
    effects: EffectApplier
    policy: OutputPolicyPort
    episodes: EpisodeAccess
    anchor: TurnAnchor
    origins: PersonOrigin
    prompts: PromptPort
    inference: InferencePort


@dataclass(frozen=True, slots=True)
class Bounds:
    max_queued_turns: int
    max_context_messages: int
    max_bytes: int
    json: JsonBounds


class _Never:
    def is_cancelled(self) -> bool:
        return False


class Conversations:
    def __init__(
        self,
        *,
        state: Store,
        actions: RuntimeActions,
        views: ReadSnapshotFactory,
        uows: UnitOfWorkFactory,
        ids: IdFactory,
        clock: Clock,
        releases: ModelReleaseReader,
        ports: TurnPorts,
        bounds: Bounds,
    ) -> None:
        self.state, self.actions, self.views, self.uows = state, actions, views, uows
        self.ids, self.clock, self.releases, self.ports, self.bounds = ids, clock, releases, ports, bounds
        self.pending: queue.Queue[str] = queue.Queue(maxsize=bounds.max_queued_turns)
        self.closed = threading.Event()
        self.thread: threading.Thread | None = None
        self._boundaries: dict[str, TurnBoundary] = {}

    # HTTP ------------------------------------------------------------------------------------------------------
    def open(self, session: SessionContext, request: CommandRequest) -> HttpResult:
        person = _crew(session)
        context = request.body.get("context")
        if not isinstance(context, Mapping):
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "context required")
        kind, ref = context.get("kind"), context.get("ref_id")
        if kind != "episode" or not isinstance(ref, str):
            # This build runs the episode intake only; other contexts refuse honestly.
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "conversation context not implemented")
        with self.uows.begin(actor=session.actor, correlation_id=request.identity.key) as uow:
            if not self.ports.episodes.visible_to(uow, ref, session.actor):
                raise ProblemError(ErrorCode.NOT_FOUND, "episode not found")
            current = self.state.open_conversation(uow, person, kind, ref)
            if current is None:
                release = self.releases.active()
                if release is None:
                    raise ProblemError(ErrorCode.MODEL_UNAVAILABLE, "no model release")
                current = Conversation(
                    self.ids.new("cnv"),
                    person,
                    "episode",
                    ref,
                    uow.release.release_id,
                    release.release_id,
                    self._partition()[1],
                    uow.now,
                )
                self.state.create_conversation(uow, current)
            body = self._body(uow, current)
        return HttpResult(body, 201, {"Location": "/v1/assistant/conversations/" + current.conversation_id})

    def get(self, session: SessionContext, conversation_id: str) -> HttpResult:
        with self.views.snapshot() as view:
            current = self._own(view, session, conversation_id)
            return HttpResult(self._body(view, current), 200, {"ETag": str(current.revision)})

    def send(self, session: SessionContext, conversation_id: str, request: CommandRequest) -> HttpResult:
        text = request.body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "reply_to_action not implemented")
        with self.views.snapshot() as view:
            running = self.actions.current(view).runtime.state.value == "running"
        if not running or self.pending.full():
            raise ProblemError(ErrorCode.MODEL_UNAVAILABLE, "assistant not running")
        with self.uows.begin(actor=session.actor, correlation_id=request.identity.key) as uow:
            current = self._own(uow, session, conversation_id)
            if current.state != "open" or self.state.active(uow, conversation_id) is not None:
                raise ProblemError(ErrorCode.INVALID_TRANSITION, "turn active")
            message = _message(self.ids.new("msg"), conversation_id, None, "person", uow.now, text=text)
            self.state.append_message(uow, message)
            turn_id = self.ids.new("trn")
            self.state.create_work(uow, self._work(uow, session, current, turn_id))
        try:
            self.pending.put_nowait(turn_id)
        except queue.Full:
            self._finish(session, turn_id, Stopped("failed", ErrorCode.MODEL_UNAVAILABLE, "queue_full"))
        return HttpResult(message, 202)

    # Worker ----------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="assistant-turns", daemon=True)
            self.thread.start()

    def stop(self, timeout: float) -> bool:
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return True

    def _loop(self) -> None:
        while not self.closed.is_set():
            try:
                turn_id = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            self.run(turn_id)

    def run(self, turn_id: str) -> None:
        with self.views.snapshot() as view:
            work = self.state.work(view, turn_id)
        if work is None or work.session is None or work.conversation_id is None:
            return
        session = work.session
        try:
            outcome = self._execute(work, session)
        except ProblemError as error:
            outcome = Stopped("failed", error.code, "dependency_failed")
        except AssistantRefused:
            outcome = Stopped("rejected", ErrorCode.MODEL_OUTPUT_REJECTED, "validation_rejected")
        self._finish(session, turn_id, outcome)

    def _execute(self, work: Work, session: SessionContext) -> Completed | Stopped:
        with self.uows.begin(actor=session.actor, correlation_id=work.turn_id) as uow:
            before = self.state.work(uow, work.turn_id)
            if before is None or before.state != TurnState.QUEUED:
                return Stopped("failed", ErrorCode.INVALID_TRANSITION, "not_queued")
            self.state.update_work(uow, before, replace(before, state=TurnState.RUNNING))
            policy = Policy.parse(
                uow.release.document(ConfigName.ASSISTANT_POLICY).raw,
                max_bytes=self.bounds.max_bytes,
                max_iterations=32,
                max_timeout_seconds=600,
            )
            conversation_id = cast(str, work.conversation_id)
            people = tuple(
                (str(body.get("message_id")), str(body.get("text")))
                for body in self.state.messages(uow, conversation_id)
                if body.get("role") == "person" and isinstance(body.get("text"), str)
            )
        scope = self._scope(work, session, policy)
        boundary = TurnBoundary(
            facts=TurnFacts(conversation_id, "episode", people),
            uows=self.uows,
            gateway=self.ports.gateway,
            policy=self.ports.policy,
            new_call_id=lambda: self.ids.new("tcl"),
            max_context_messages=self.bounds.max_context_messages,
        )
        self._boundaries[work.turn_id] = boundary
        controller = Controller(
            clock=self.clock,
            releases=self.releases,
            prompts=self.ports.prompts,
            inference=self.ports.inference,
            boundary=boundary,
        )
        transcript = tuple(
            TranscriptItem("user", text) for _, text in people[-self.bounds.max_context_messages :]
        )
        plan = Plan(
            scope, policy, ContextBudget(8192, 1024, ("facts", "evidence")), transcript, self.bounds.json
        )
        return controller.run(plan, cancel=_Never(), progress=_ignore)

    def _scope(self, work: Work, session: SessionContext, policy: Policy) -> ToolScope:
        mission, origin = self._partition()
        anchor = self.ports.anchor.anchor(origin)
        if anchor is None:
            raise ProblemError(ErrorCode.MODEL_UNAVAILABLE, "hub clock not captured")
        return ToolScope(
            session,
            work.subject_person_id,
            mission,
            origin,
            work.episode_id,
            None,
            None,
            work.turn_id,
            cast(str, work.conversation_id),
            work.config_release_id,
            work.evidence_release_id,
            work.model_release_id,
            Phase.INTAKE,
            work.source_revision,
            1,
            ElapsedDeadline(anchor, timedelta(seconds=policy.turn_timeout_seconds)),
        )

    # Commit ----------------------------------------------------------------------------------------------------
    def _finish(self, session: SessionContext, turn_id: str, outcome: Completed | Stopped) -> None:
        boundary = self._boundaries.pop(turn_id, None)
        calls = [] if boundary is None else boundary.seen.calls
        try:
            self._commit(session, turn_id, outcome, calls, boundary)
        except (ProblemError, AssistantRefused) as error:
            code = error.code if isinstance(error, ProblemError) else ErrorCode.MODEL_OUTPUT_REJECTED
            self._commit(session, turn_id, Stopped("failed", code, "commit_failed"), calls, None)

    def _commit(
        self,
        session: SessionContext,
        turn_id: str,
        outcome: Completed | Stopped,
        calls: list[dict[str, object]],
        boundary: TurnBoundary | None,
    ) -> None:
        with self.uows.begin(actor=session.actor, correlation_id=turn_id) as uow:
            work = self.state.work(uow, turn_id)
            if work is None or work.conversation_id is None:
                return
            for call in calls:
                uow.connection.execute(
                    "INSERT OR IGNORE INTO assistant_tool_call VALUES (?,?,?)",
                    (turn_id, str(call["tool_call_id"]), canonical_json(call)),
                )
            if isinstance(outcome, Completed):
                message, state, code = (
                    self._completed(uow, work, outcome, boundary),
                    TurnState.COMPLETED,
                    None,
                )
            else:
                message, state, code = (
                    self._stopped(uow, work, outcome),
                    TurnState(outcome.state),
                    outcome.code,
                )
            self.state.append_message(uow, message)
            if work.state in (TurnState.QUEUED, TurnState.RUNNING):
                self.state.update_work(
                    uow, work, replace(work, state=state, error_code=code, steps_used=_steps(outcome))
                )

    def _completed(
        self, uow: UnitOfWork, work: Work, outcome: Completed, boundary: TurnBoundary | None
    ) -> dict[str, object]:
        action = dict(outcome.candidate.payload)
        if outcome.candidate.effects:
            # The proposal becomes a *proposed* E05 request the person accepts or declines on E05's route; the
            # assistant never starts it. Its ID is kept in the turn record (the message schema has no field yet,
            # request R-E13 in docs/tdd/E13.md).
            applied = self.ports.effects.apply(uow, outcome.scope, outcome.candidate.effects, work.turn_id)
            uow.connection.execute(
                "INSERT INTO assistant_applied_effect VALUES (?,?,?)",
                (
                    work.turn_id,
                    outcome.candidate.effects[0].tool_call_id,
                    canonical_json(
                        {"request_ids": list(applied.request_ids), "gap_ids": list(applied.gap_ids)}
                    ),
                ),
            )
        kit = {} if boundary is None else boundary.seen.kit
        message = _message(
            self.ids.new("msg"),
            cast(str, work.conversation_id),
            work.turn_id,
            "assistant",
            uow.now,
            action=action,
        )
        message["limitations"] = kit_limitations(uow, kit)
        return message

    def _stopped(self, uow: UnitOfWork, work: Work, outcome: Stopped) -> dict[str, object]:
        copy = json.loads(uow.release.document(ConfigName.UI_COPY).raw)
        text = copy["designed_states"]["assistant_stopped"]
        status = {"stale": "failed"}.get(outcome.state, outcome.state)
        return _message(
            self.ids.new("msg"),
            cast(str, work.conversation_id),
            work.turn_id,
            "notice",
            uow.now,
            text=str(text),
            status=status,
            error_code=outcome.code.value,
        )

    # Helpers ---------------------------------------------------------------------------------------------------
    def _own(self, view: object, session: SessionContext, conversation_id: str) -> Conversation:
        person = _crew(session)
        current = self.state.conversation(view, conversation_id)  # type: ignore[arg-type]
        if current is None or current.person_id != person:
            raise ProblemError(ErrorCode.NOT_FOUND, "conversation not found")
        return current

    def _work(self, uow: UnitOfWork, session: SessionContext, current: Conversation, turn_id: str) -> Work:
        release = self.releases.get(current.model_release_id).document
        evaluated = release.get("evaluated_with")
        evidence = evaluated.get("evidence_release_id") if isinstance(evaluated, dict) else None
        if not isinstance(evidence, str):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "model release has no evidence pin")
        return Work(
            turn_id,
            self.ids.new("task"),
            "conversation",
            current.conversation_id,
            current.context_ref_id,
            None,
            None,
            current.person_id,
            session.actor,
            session,
            "intake",
            0,
            uow.release.release_id,
            evidence,
            current.model_release_id,
            current.revision + 1,
            uow.now,
        )

    def _partition(self) -> tuple[str, Literal["LIVE", "REPLAY", "SIM"]]:
        value = self.ports.origins.partition()
        if value is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "roster partition unknown")
        return value

    def _body(self, view: object, current: Conversation) -> dict[str, object]:
        active = self.state.active(view, current.conversation_id)  # type: ignore[arg-type]
        return {
            "conversation_id": current.conversation_id,
            "revision": current.revision,
            "person_id": current.person_id,
            "context": {"kind": current.context_kind, "ref_id": current.context_ref_id},
            "state": current.state,
            "model_release_id": current.model_release_id,
            "config_release_id": current.config_release_id,
            "created_at": _instant(current.created_at),
            "messages": [dict(item) for item in self.state.messages(view, current.conversation_id)],  # type: ignore[arg-type]
            "active_turn_id": None if active is None else active.turn_id,
            "allowed_actions": ["send"] if active is None and current.state == "open" else [],
        }


def _crew(session: SessionContext) -> str:
    if session.kind != "crew" or session.actor.kind != "person" or session.person_id != session.actor.id:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "crew session required")
    return session.actor.id


def _message(
    message_id: str,
    conversation_id: str,
    turn_id: str | None,
    role: str,
    at: datetime,
    *,
    text: str | None = None,
    action: Mapping[str, object] | None = None,
    status: str = "complete",
    error_code: str | None = None,
) -> dict[str, object]:
    return {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "role": role,
        "created_at": _instant(at),
        "text": text,
        "action": None if action is None else dict(action),
        "status": status,
        "error_code": error_code,
        "evidence": [],
        "limitations": [],
    }


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _steps(outcome: Completed | Stopped) -> int:
    return outcome.steps_used if isinstance(outcome, Completed) else 0


def _ignore(progress: StepProgress) -> None:
    return None


ConversationsFactory = Callable[[], Conversations]
