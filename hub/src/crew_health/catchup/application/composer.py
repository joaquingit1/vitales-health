"""CONTRACTS 26, SYSTEM 8.1 and A6: one pure composition of the help snapshot and its pinned templates.

Preview, the press response and every read render the same lines because they read only the snapshot and the
document versions it pins. Reads never write: the durable subscriber on help's queued event freezes the approved
catch-up, in its own transaction, from the snapshot help stored at the press.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from crew_health.catchup.application.bounded import BoundedCall
from crew_health.catchup.application.config import PinnedWords, pinned_versions, pins_key
from crew_health.catchup.application.ports import ConsentQueries, PlanCatchUpPort, PlanLinkView
from crew_health.catchup.application.quarantine import Quarantine
from crew_health.catchup.application.repository import CatchUpRepository, Message
from crew_health.catchup.application.settings import CatchUpSettings
from crew_health.catchup.domain.compose import Composition, compose_for_help
from crew_health.catchup.domain.errors import CatchUpUnavailable, CompositionTooLarge
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.contracts_gen.schemas.ground_message_schema import GroundMessage
from crew_health.groundlink.application.ports import BundleOutbox, GroundLinkQuery
from crew_health.help.application.ports import HelpLogSnapshots, HelpLogSnapshotView, LinkSnapshot
from crew_health.kernel.clock import Clock
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import ReadSnapshotFactory, StoredEvent, UnitOfWork, UnitOfWorkFactory

SUBSCRIBER = "catchup.ground_message"


@dataclass(frozen=True, slots=True)
class CatchUpDeps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    settings: CatchUpSettings
    clock: Clock
    ids: IdFactory
    repository: CatchUpRepository
    link: GroundLinkQuery | None = None
    outbox: BundleOutbox | None = None
    help_snapshots: HelpLogSnapshots | None = None
    config: ConfigReader | None = None
    plans: PlanCatchUpPort | None = None
    consent: ConsentQueries | None = None


def _instant(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat().replace("+00:00", "Z")


class CatchUpComposer:
    def __init__(self, deps: CatchUpDeps) -> None:
        self.deps = deps
        self.words = PinnedWords(deps.config)
        self._link_call = BoundedCall(deps.settings.port_timeout_ms)
        self.snapshot_call = BoundedCall(deps.settings.port_timeout_ms)
        self.quarantine = Quarantine(
            SUBSCRIBER, deps.settings.subscriber_max_attempts, deps.repository.quarantine
        )

    def link(self) -> LinkSnapshot:
        return self._link_call.call(self._read_link)

    def _read_link(self) -> LinkSnapshot:
        query = self.deps.link
        if query is None:
            raise CatchUpUnavailable()
        with self.deps.snapshots.snapshot() as view:
            link = query.current(view)
        if link is None:
            raise CatchUpUnavailable()
        # Only the provider's ports may cross modules. This matches the GroundLink wire exactly.
        contract: dict[str, object] = {
            "status": link.status,
            "one_way_delay_s": link.one_way_delay_s,
            "next_contact_at": _instant(link.next_contact_at),
            "last_contact_at": _instant(link.last_contact_at),
            "source": link.source,
            "environment_id": link.environment_id,
            "delay_text": link.delay_text,
            "delay_source_ids": list(link.delay_source_ids),
            "gap_kind": link.gap_kind,
            "gap_planned": link.gap_planned,
        }
        return LinkSnapshot(link.status, link.next_contact_at, link.source != "measured", contract)

    def plan_link(self) -> PlanLinkView:
        """E28's ground_log line: the link in one read snapshot, at that snapshot's time. No provider or an unknown
        state raises CatchUpUnavailable."""
        query = self.deps.link
        if query is None:
            raise CatchUpUnavailable()
        with self.deps.snapshots.snapshot() as view:
            link = query.current(view)
            observed_at = view.now
        if link is None:
            raise CatchUpUnavailable()
        return PlanLinkView(
            link.status, link.next_contact_at, link.one_way_delay_s, link.source != "measured", observed_at
        )

    def for_help(self, snapshot: HelpLogSnapshotView) -> Composition:
        """Help's preview, press response and reads: the frozen copy once it exists, else the same pure render."""
        deps = self.deps
        with deps.snapshots.snapshot() as view:
            row = deps.repository.for_revision(view, snapshot.help_session_id, snapshot.session_revision)
            if row is not None and row.state == "frozen":
                return deps.repository.lines(view, row.ground_message_id)
        return self.compose(snapshot)[0]

    def compose(self, snapshot: HelpLogSnapshotView) -> tuple[Composition, str]:
        versions = pinned_versions(snapshot.pins)
        words = self.words.words(versions)
        limit = self.deps.settings.max_lines
        if len(snapshot.ticks) + (snapshot.crew_called_at is not None) > limit:
            raise CompositionTooLarge()
        lines = compose_for_help(
            snapshot,
            words.catch,
            words.ground,
            crew_time_text=snapshot.crew_time_text,
            plan=None,
            helping=snapshot.helping,
        )
        if any(len(section) > limit for section in lines.sections()):
            raise CompositionTooLarge()
        return lines, pins_key(versions)

    def on_ground_message_changed(self, uow: UnitOfWork, event: StoredEvent) -> None:
        if event.type != EventType.GROUND_MESSAGE_CHANGED or event.scope.kind != "crew":
            return
        self.quarantine.run(uow, event, self._record_and_freeze)

    def _record_and_freeze(self, uow: UnitOfWork, event: StoredEvent) -> None:
        try:
            data: object = json.loads(event.data_json)
            if not isinstance(data, dict) or data.get("state") != "queued":
                return
            message = GroundMessage.model_validate_json(event.data_json)
        except (ValueError, ValidationError) as error:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value) from error
        if message.help_session_id is None:
            raise CatchUpUnavailable()
        row = Message(
            message.ground_message_id.root,
            message.help_session_id.root,
            message.session_revision,
            message.queued_at.root,
            message.lines[0].root,
            message.simulated,
        )
        self.deps.repository.insert(uow, row)
        stored = self.deps.repository.message(uow, row.ground_message_id)
        if stored is not None and stored.state == "frozen":
            return
        provider = self.deps.help_snapshots
        if provider is None:
            # Without help's snapshot port the row stays pending; reads compose it or fail retryably.
            return
        snapshot = self.snapshot_call.call(lambda: provider.for_message(row.ground_message_id))
        if snapshot is None or (snapshot.help_session_id, snapshot.session_revision) != (
            row.help_session_id,
            row.session_revision,
        ):
            raise CatchUpUnavailable()
        lines, pinned = self.compose(snapshot)
        self.deps.repository.freeze(uow, row.ground_message_id, lines, pinned, snapshot.subject_person_id)


# Strict RFC 3339 Z form at the HTTP boundary, not datetime.fromisoformat's permissive input language.
def parse_since(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value) is None:
        raise ValueError("invalid instant")
    return datetime.fromisoformat(value)
