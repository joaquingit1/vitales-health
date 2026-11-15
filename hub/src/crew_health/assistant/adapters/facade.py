from __future__ import annotations

import hashlib
from collections.abc import Iterator

from crew_health.assistant.adapters.conversations import Conversations
from crew_health.assistant.adapters.runtime_worker import RuntimeWorker
from crew_health.assistant.adapters.staging import ReadStage
from crew_health.assistant.application.assessment_queries import AssessmentQueries
from crew_health.assistant.application.http_ports import CommandRequest, HttpResult, QueryParameters
from crew_health.assistant.application.runtime_actions import RuntimeActions
from crew_health.assistant.application.state_ports import State
from crew_health.contracts_gen.enums import ConfigName, Role
from crew_health.kernel.commands import AcceptedReceipt, IdempotencyStore, StoredResult
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWorkFactory
from crew_health.model.application.ports import JsonValue, ModelReleaseReader


class AssistantFacade:
    def __init__(
        self,
        *,
        state: State,
        actions: RuntimeActions,
        queries: AssessmentQueries,
        views: ReadSnapshotFactory,
        uows: UnitOfWorkFactory,
        ids: IdFactory,
        ledger: IdempotencyStore,
        releases: ModelReleaseReader,
        worker: RuntimeWorker | None,
        max_bytes: int,
        conversations: Conversations | None = None,
    ) -> None:
        self.conversations = conversations
        (
            self.state,
            self.actions,
            self.queries,
            self.views,
            self.uows,
            self.ids,
            self.ledger,
            self.releases,
            self.worker,
            self.max_bytes,
        ) = state, actions, queries, views, uows, ids, ledger, releases, worker, max_bytes

    def query(
        self, session: SessionContext, operation: str, target: str | None, parameters: QueryParameters
    ) -> HttpResult:
        if session.actor.kind == "assistant":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "human session required")
        if operation == "getAssistantStatus":
            return self.status()
        if operation == "getConversation" and target is not None and self.conversations is not None:
            return self.conversations.get(session, target)
        if operation == "getAssessment" and target is not None:
            with self.views.snapshot() as view:
                return HttpResult(self.queries.get(view, session, target), 200)
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant capability not implemented")

    def status(self) -> HttpResult:
        with self.views.snapshot() as view:
            current = self.actions.current(view)
            policy = view.release.document(ConfigName.ASSISTANT_POLICY)
            queue = len(self.state.pending(view))
        info = None
        if current.model_release_id is not None:
            document = self.releases.get(current.model_release_id).document
            model = _object(document.get("model"))
            digest = (
                _object(document.get("weights")).get("weights_sha256")
                if current.mode == "live"
                else document.get("stub_script_sha256")
            )
            info = {
                "model_release_id": current.model_release_id,
                "model_id": model.get("model_id"),
                "revision": model.get("revision"),
                "sha256": digest,
                "prompt_version": document.get("prompt_version"),
                "tool_catalog_version": document.get("tool_catalog_version"),
                "mode": current.mode,
            }
        error: dict[str, object] | None = None
        if current.error_code is not None:
            error = {
                "code": current.error_code.value,
                "message": current.error_code.value,
                "retryable": True,
                "field_errors": [],
                "current_state": None,
                "supported_alternative": None,
                "correlation_id": self.ids.new("corr"),
            }
        return HttpResult(
            {
                "state": current.runtime.state.value,
                "model_release": info,
                "since": current.since.isoformat().replace("+00:00", "Z"),
                "last_error": error,
                "queue_depth": queue,
                "policy_version": hashlib.sha256(policy.raw).hexdigest(),
            },
            200,
        )

    def command(
        self, session: SessionContext, operation: str, target: str | None, request: CommandRequest
    ) -> HttpResult:
        if operation == "openConversation" and self.conversations is not None:
            return self.conversations.open(session, request)
        if operation == "sendMessage" and target is not None and self.conversations is not None:
            return self.conversations.send(session, target, request)
        if operation != "setAssistantRuntime":
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant capability not implemented")
        if not (
            session.kind == "operator" and session.actor.kind == "operator" and Role.OPERATOR in session.roles
        ):
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "operator required")
        if request.identity.actor_ref != session.actor.id:
            raise ProblemError(ErrorCode.IDENTITY_MISMATCH, "command identity mismatch")
        with self.views.snapshot() as view:
            stage = ReadStage(
                view,
                release=view.release,
                actor=session.actor,
                correlation_id=request.identity.key,
                max_audits=1,
                max_entry_bytes=self.max_bytes,
            )
            prior = self.ledger.lookup(stage, request.identity)
            stage.discard()
        if prior is not None:
            return self.status()
        action = request.body.get("action")
        if action not in {"start", "stop"}:
            raise ProblemError(ErrorCode.INVALID_VALUE, "runtime action invalid")
        release = self.releases.active() if action == "start" else None
        with self.uows.begin(
            actor=session.actor, command=request.identity, correlation_id=request.identity.key
        ) as uow:
            prior = self.ledger.lookup(uow, request.identity)
            if prior is None:
                if (
                    request.config_release_id is not None
                    and request.config_release_id != uow.release.release_id
                ):
                    raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, "configuration changed")
                self.actions.request(uow, start=action == "start", release=release)
                receipt = AcceptedReceipt(
                    request.identity.key,
                    operation,
                    target or "assistant.runtime",
                    self.ids.new("receipt"),
                    uow.now,
                )
                self.ledger.record(
                    uow, request.identity, StoredResult(receipt, 200, "assistant.runtime", "accepted", None)
                )
        if self.worker is not None:
            self.worker.notify()
        return self.status()

    def stream(self, session: SessionContext, conversation_id: str) -> Iterator[bytes]:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant streaming not implemented")


def _object(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "model metadata unavailable")
    return value
