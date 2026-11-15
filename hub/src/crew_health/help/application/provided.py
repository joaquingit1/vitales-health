"""Ports help provides to other modules (SYSTEM 4.2, 4.4, 8.1; E26 4.3).

`HelpSessionReader.context` and `.readable_by` keep the names SYSTEM 4.4 gives them (E30, E31, E32 consume them).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from crew_health.help.application.ground_flow import GroundMessages
from crew_health.help.application.opening import OpenHelp, OpenRequest
from crew_health.help.application.ports import HelpContext, HelpFlowRef, HelpSessionSnapshot
from crew_health.help.application.support import HelpDeps, bounds, viewer_of
from crew_health.help.domain.access import can_read
from crew_health.help.domain.ground import EventOutcome, GroundEvent, HelpLogSnapshot
from crew_health.help.domain.session import ActorKind, ActorRef
from crew_health.kernel.uow import Actor, SessionContext

RED_FLAG_OPERATION = "internal.help_red_flag_entry"


class SessionReader:
    def __init__(self, deps: HelpDeps) -> None:
        self._deps = deps

    def context(self, session: SessionContext, help_session_id: str) -> HelpContext:
        with self._deps.snapshots.snapshot() as snap:
            found = self._deps.sessions.get(snap, help_session_id)
        if found is None or not can_read(found, viewer_of(session)):
            return HelpContext(False, None, None)
        return HelpContext(True, found.origin_mode, found.environment_id)

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        return self.context(session, help_session_id).visible

    def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
        with self._deps.snapshots.snapshot() as snap:
            found = self._deps.sessions.get(snap, help_session_id)
        if found is None:
            return None
        pins = {pin.name: pin.version for pin in found.config_pins}
        opener = found.opened_by.id if found.opened_by.kind == "person" else None
        return HelpSessionSnapshot(
            help_session_id=found.help_session_id,
            revision=found.revision,
            open=found.is_open,
            subject_person_id=found.subject_person_id,
            opener_person_id=opener,
            helper_ids=frozenset(found.helper_ids()),
            flow_id=found.node_id if found.node_kind == "flow" else None,
            ui_copy_version=pins.get("ui-copy", ""),
            closed_at=found.closed_at,
        )

    def qualifying_sessions(self) -> Sequence[HelpFlowRef]:
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            rows = tuple(deps.sessions.open_sessions(snap, bounds(deps).max_open_sessions))
        return tuple(
            HelpFlowRef(
                s.help_session_id,
                s.node_kind,
                s.node_id,
                tuple((p.name, p.version) for p in s.config_pins),
                s.subject_person_id,
                s.origin_mode,
            )
            for s in rows
        )


class HelpEntry:
    """HelpEntryPort (SYSTEM 8.1): E11's durable `open_help` effect opens or joins, opener `engine`."""

    def __init__(self, opener: OpenHelp) -> None:
        self._opener = opener

    def open_from_red_flag(
        self,
        episode_id: str,
        answer_id: str,
        subject_person_id: str,
        flow_id: str,
        client_time: datetime,
        idempotency_key: str,
    ) -> str:
        request = OpenRequest(
            subject_kind="crewmate",
            subject_person_id=subject_person_id,
            entry_kind="red_flag",
            entry_ref_id=episode_id,
            place=None,
            client_opened_at=client_time,
            offline_path=(),
            flow_id=flow_id,
            client_release_id=None,
        )
        return self._opener.open_for_engine(
            "pathways",
            request,
            key=idempotency_key,
            operation=RED_FLAG_OPERATION,
            correlation_id=idempotency_key,
        )


class LogSnapshots:
    """The stored snapshot a ground message was drafted from (E29 composes catch-up from it)."""

    def __init__(self, deps: HelpDeps) -> None:
        self._deps = deps

    def for_message(self, ground_message_id: str) -> HelpLogSnapshot | None:
        with self._deps.snapshots.snapshot() as snap:
            return self._deps.messages.snapshot(snap, ground_message_id)


class GroundMessageLifecycle:
    """GroundMessageLifecyclePort (E26 4.3) for E29: applied, duplicate or buffered; nothing discarded."""

    def __init__(self, ground: GroundMessages) -> None:
        self._ground = ground

    def mark_sent(
        self,
        message_id: str,
        event_id: str,
        sent_at: datetime,
        one_way_delay_s: float | None,
        simulated: bool,
    ) -> EventOutcome:
        event = GroundEvent(event_id, "sent", sent_at, delay_s=one_way_delay_s, simulated=simulated)
        return self._ground.apply_event(message_id, event)

    def record_arrival(self, message_id: str, event_id: str, arrived_at: datetime) -> EventOutcome:
        return self._ground.apply_event(
            message_id, GroundEvent(event_id, "arrived", arrived_at, arrived_at=arrived_at)
        )

    def record_receipt(
        self, message_id: str, event_id: str, received_at: datetime, arrived_at: datetime
    ) -> EventOutcome:
        return self._ground.apply_event(
            message_id, GroundEvent(event_id, "receipt", received_at, arrived_at=arrived_at)
        )

    def record_reply(
        self, message_id: str, event_id: str, text: str, by: Actor, replied_at: datetime, arrived_at: datetime
    ) -> EventOutcome:
        kind: ActorKind = by.kind
        event = GroundEvent(
            event_id, "reply", replied_at, arrived_at=arrived_at, text=text, by=ActorRef(kind, by.id, None)
        )
        return self._ground.apply_event(message_id, event)
