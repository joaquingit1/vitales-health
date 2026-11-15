"""CONTRACTS 12 and 26: pinned link state and durable approved records."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, EventType, Role
from crew_health.contracts_gen.schemas.requests_schema import GroundLinkDemoSwitch, GroundLinkDemoSwitch1
from crew_health.groundlink.application.config import LinkConfigCache
from crew_health.groundlink.application.ports import (
    BundleStatus,
    GroundLinkView,
    InboundItem,
    LinkGap,
    MissionStatePort,
    OutboxItem,
    RefKind,
)
from crew_health.groundlink.application.repository import Ack, GroundLinkRepository
from crew_health.groundlink.application.settings import GroundLinkSettings
from crew_health.groundlink.application.wire import link_wire
from crew_health.groundlink.domain.link import DemoOverride, resolve, validate_demo
from crew_health.groundlink.domain.queue import Bundle, check_same_content
from crew_health.help.application.ports import GroundMessageLifecyclePort
from crew_health.kernel.clock import Clock, require_utc
from crew_health.kernel.commands import (
    AcceptedReceipt,
    IdempotencyStore,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)


def unavailable() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)


def invalid(field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.INVALID_VALUE,
        ErrorCode.INVALID_VALUE.value,
        field_errors=(FieldError(field, "invalid", ErrorCode.INVALID_VALUE.value),),
    )


@dataclass
class GroundLinkDeps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    settings: GroundLinkSettings
    ids: IdFactory
    clock: Clock
    idempotency: IdempotencyStore
    repository: GroundLinkRepository
    config: LinkConfigCache
    mission: MissionStatePort | None
    lifecycle: GroundMessageLifecyclePort | None


def emit_link(uow: UnitOfWork, link: GroundLinkView) -> None:
    uow.emit(
        OutboxEvent(
            EventType.GROUND_LINK_CHANGED,
            uow.now,
            EventScope("crew", None, link.environment_id),
            uow.release.release_id,
            uow.correlation_id,
            canonical_json(link_wire(link)),
            Audience(
                "groundlink.link", "ground-link", AudienceProjectionKey("groundlink", "ground_link", 1), "1"
            ),
        )
    )


class GroundLinkReader:
    def __init__(self, deps: GroundLinkDeps) -> None:
        self.deps = deps

    def environment(self, view: ReadSnapshot) -> str | None:
        mission = self.deps.mission
        facts = None if mission is None else mission.current(view)
        return facts.active_environment_id if facts is not None else self.deps.repository.environment(view)

    def resolve_for(
        self, view: ReadSnapshot, environment: str, override: DemoOverride | None
    ) -> GroundLinkView:
        cfg = self.deps.config.get(view, environment)
        state = resolve(
            cfg.profile,
            override,
            view.now,
            last_contact_at=self.deps.repository.last_contact(view),
            delay_text=cfg.delay_text,
            delay_source_ids=cfg.delay_source_ids,
        )
        return GroundLinkView(
            state.status,
            state.one_way_delay_s,
            state.next_contact_at,
            state.last_contact_at,
            state.source,
            state.environment_id,
            state.delay_text,
            state.delay_source_ids,
            state.gap_kind,
            state.gap_planned,
        )

    def current(self, view: ReadSnapshot) -> GroundLinkView | None:
        environment = self.environment(view)
        if environment is None:
            return None
        mission = self.deps.mission
        facts = None if mission is None else mission.current(view)
        override = (
            self.deps.repository.override(view)
            if facts is not None and facts.mission_mode in ("SIM", "REPLAY")
            else None
        )
        return self.resolve_for(view, environment, override)

    def last_gap(self, view: ReadSnapshot) -> LinkGap | None:
        return self.deps.repository.last_gap(view)


class GroundLinkCommands:
    def __init__(self, deps: GroundLinkDeps, reader: GroundLinkReader) -> None:
        self.deps, self.reader = deps, reader

    def set_demo(
        self, session: SessionContext, body: GroundLinkDemoSwitch, *, key: str, correlation_id: str
    ) -> tuple[int, bytes]:
        if session.actor.kind not in ("person", "operator") or Role.OPERATOR not in session.roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
        with self.deps.snapshots.snapshot() as view:
            mission = self.deps.mission
            facts = None if mission is None else mission.current(view)
            if facts is None or facts.mission_mode not in ("SIM", "REPLAY"):
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            environment = facts.active_environment_id
        identity = command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation="demoSetGroundLink",
            target="ground-link",
            parameters_json=b"{}",
            body_fingerprint=hashlib.sha256(
                canonical_json(body.model_dump(mode="json", exclude_unset=True))
            ).hexdigest(),
            fingerprint_kind="sha256",
        )
        with self.deps.uow.begin(actor=session.actor, command=identity, correlation_id=correlation_id) as uow:
            stored = self.deps.idempotency.lookup(uow, identity)
            if stored is not None:
                return stored.status, self._replay(uow, stored)
            seq, response = self._apply(uow, body, environment)
            receipt = AcceptedReceipt(
                key, identity.operation, identity.target, self.deps.ids.new("glc"), uow.now
            )
            # E01's ledger keeps safe bodies only for release operations: the reference names the history row
            # this command wrote, and a retry rebuilds the response from that row (SYSTEM 5.8).
            self.deps.idempotency.record(
                uow, identity, StoredResult(receipt, 200, f"gls_{seq}", "accepted", None)
            )
            return 200, response

    def _replay(self, view: ReadSnapshot, stored: StoredResult) -> bytes:
        if stored.safe_body is not None:
            return stored.safe_body
        ref = stored.resource_ref or ""
        seq = int(ref[4:]) if ref.startswith("gls_") and ref[4:].isdigit() else None
        link = None if seq is None else self.deps.repository.recorded(view, seq)
        if link is None:
            # A demo replay whose history was pruned answers retryable INTERNAL_ERROR.
            raise unavailable()
        return canonical_json(link_wire(link))

    def _apply(self, uow: UnitOfWork, body: GroundLinkDemoSwitch, environment: str) -> tuple[int, bytes]:
        override = None
        if not isinstance(body.root, GroundLinkDemoSwitch1):
            b = body.root
            override = DemoOverride(
                b.status,
                None if b.one_way_delay_s is None else b.one_way_delay_s.root,
                None if b.next_contact_at is None else b.next_contact_at.root,
                None if b.gap_kind is None else b.gap_kind.root,
                bool(b.gap_planned),
                uow.now,
            )
            validate_demo(
                override.status,
                override.one_way_delay_s,
                override.next_contact_at,
                override.gap_kind,
                override.gap_planned,
            )
        before = self.reader.resolve_for(uow, environment, self.deps.repository.override(uow))
        after = self.reader.resolve_for(uow, environment, override)
        seq = self.deps.repository.append(
            uow, "clear" if override is None else "override", uow.now, after, override
        )
        if before != after:
            emit_link(uow, after)
        return seq, canonical_json(link_wire(after))

    def on_environment_activated(self, uow: UnitOfWork, event: StoredEvent) -> None:
        data: object = json.loads(event.data_json)
        if not isinstance(data, dict):
            return
        environment = data.get("environment_id")
        if environment is None and isinstance(data.get("profile"), dict):
            environment = data["profile"].get("environment_id")
        if not isinstance(environment, str):
            return
        latest_at = self.deps.repository.environment_at(uow)
        if latest_at is not None and event.occurred_at < latest_at:
            return
        history = self.deps.repository.history(uow)
        before = history[-1].link if history else None
        after = self.reader.resolve_for(uow, environment, None)
        self.deps.repository.append(uow, "environment", event.occurred_at, after)
        if before != after:
            emit_link(uow, after)

    def on_ground_message_changed(self, uow: UnitOfWork, event: StoredEvent) -> None:
        if event.scope.kind != "crew":
            return
        data: object = json.loads(event.data_json)
        if not isinstance(data, dict) or data.get("state") != "queued":
            return
        ref, sha, queued, simulated = (
            data.get(k) for k in ("ground_message_id", "text_sha256", "queued_at", "simulated")
        )
        if (
            not isinstance(ref, str)
            or not isinstance(sha, str)
            or not isinstance(queued, str)
            or not isinstance(simulated, bool)
        ):
            raise invalid("/")
        Outbox(self.deps).enqueue(
            uow, OutboxItem("ground_message", ref, sha, datetime.fromisoformat(queued), simulated)
        )


def bundle_status(b: Bundle) -> BundleStatus:
    return BundleStatus(
        b.ref_kind, b.ref_id, b.state, b.queued_at, b.sent_at, b.arrived_at, b.attempts, b.simulated
    )


class Outbox:
    def __init__(self, deps: GroundLinkDeps) -> None:
        self.deps = deps

    def enqueue(self, uow: UnitOfWork, item: OutboxItem) -> str:
        require_id(item.ref_id)
        require_utc(item.queued_at)
        if (
            item.ref_kind not in ("ground_message", "handoff")
            or re.fullmatch(r"[0-9a-f]{64}", item.content_sha256) is None
        ):
            raise invalid("/content_sha256")
        existing = self.deps.repository.bundle(uow, item.ref_kind, item.ref_id)
        if existing is not None:
            check_same_content(existing.content_sha256, item.content_sha256)
            return existing.bundle_id
        bundle = Bundle(
            self.deps.ids.new("glb"),
            item.ref_kind,
            item.ref_id,
            item.content_sha256,
            item.queued_at,
            "queued",
            0,
            None,
            None,
            None,
            None,
            None,
            item.simulated,
            None,
        )
        self.deps.repository.insert_bundle(uow, bundle)
        return bundle.bundle_id

    def status(self, view: ReadSnapshot, ref_kind: RefKind, ref_id: str) -> BundleStatus | None:
        bundle = self.deps.repository.bundle(view, ref_kind, ref_id)
        return None if bundle is None else bundle_status(bundle)

    def delivered(
        self, view: ReadSnapshot, *, since: datetime | None, limit: int
    ) -> tuple[BundleStatus, ...]:
        if limit < 1:
            raise invalid("/limit")
        return tuple(bundle_status(b) for b in self.deps.repository.delivered(view, since, limit))


class Inbox:
    def __init__(self, deps: GroundLinkDeps) -> None:
        self.deps = deps

    def receive(self, item: InboundItem) -> Literal["accepted", "duplicate"]:
        with self.deps.uow.begin(actor=item.by, correlation_id=self.deps.ids.new("glc")) as uow:
            if self.deps.repository.ack_exists(uow, item.event_id):
                return "duplicate"
            require_id(item.event_id)
            require_id(item.ref_id)
            require_utc(item.sent_by_ground_at)
            if item.kind == "reply" and (
                item.text is None or len(item.text.encode("utf-8")) > self.deps.settings.max_reply_bytes
            ):
                raise invalid("/text")
            if item.kind == "receipt" and item.text is not None:
                raise invalid("/text")
            ack = Ack(self.deps.ids.new("gla"), item, "waiting", None, None, 0, None, None)
            self.deps.repository.insert_ack(uow, ack)
            return "accepted"
