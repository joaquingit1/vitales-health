"""CONTRACTS 26: transport truth selects delivered records; the ground sees only what it is granted.

Reads never write. An item whose approved catch-up is not frozen yet is rendered from help's stored snapshot with
the same pure composition; when that is not possible the whole read fails retryably instead of showing a placeholder
in place of approved content (SYSTEM 8.1). Every item is checked against the recipient's current grant for its
subject (SYSTEM 5.7.1, AGENTS section 6 privacy): ungranted items are left out, and without a consent provider the
read fails closed.
"""

from dataclasses import dataclass
from datetime import datetime

from crew_health.catchup.application.bounded import BoundedCall
from crew_health.catchup.application.composer import CatchUpComposer, CatchUpDeps
from crew_health.catchup.application.config import parse_words
from crew_health.catchup.application.ports import GROUND_LOG_SCOPE, GroundLog, LogGap, LogItem
from crew_health.catchup.domain.compose import Composition
from crew_health.catchup.domain.errors import CatchUpUnavailable, ConsentRequired, LogTooLarge
from crew_health.catchup.domain.log import select_items
from crew_health.contracts_gen.enums import ConfigName
from crew_health.groundlink.application.ports import BundleStatus
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import ReadSnapshot


@dataclass(frozen=True, slots=True)
class _Lines:
    title: str
    lines: Composition
    subject_person_id: str | None


class GroundLogService:
    def __init__(self, deps: CatchUpDeps, composer: CatchUpComposer) -> None:
        self.deps, self.composer = deps, composer
        self._transport_call = BoundedCall(deps.settings.port_timeout_ms)
        self._snapshot_call = BoundedCall(deps.settings.port_timeout_ms)

    def log(self, since: datetime | None, *, recipient: str) -> GroundLog:
        consent = self.deps.consent
        if consent is None:
            raise ConsentRequired()
        effective, gap, delivered, unknown, now = self._transport_call.call(lambda: self._delivered(since))
        if len(delivered) > self.deps.settings.log_max_items:
            raise LogTooLarge()
        items: list[LogItem] = []
        for bundle in delivered:
            found = self._lines(bundle, unknown)
            with self.deps.snapshots.snapshot() as view:
                if not self._granted(view, found.subject_person_id, recipient):
                    continue
            items.append(
                LogItem(
                    bundle.ref_kind,
                    bundle.ref_id,
                    found.title,
                    bundle.queued_at,
                    bundle.arrived_at,
                    found.lines.sections(),
                    None,
                    bundle.simulated,
                )
            )
        return GroundLog(
            effective, gap, select_items(tuple(items), effective, self.deps.settings.log_max_items), now
        )

    def _granted(self, view: ReadSnapshot, subject: str | None, recipient: str) -> bool:
        consent = self.deps.consent
        if consent is None or subject is None:
            return False
        return consent.allows(view, subject_person_id=subject, recipient=recipient, scope=GROUND_LOG_SCOPE)

    def _delivered(
        self, since: datetime | None
    ) -> tuple[datetime | None, LogGap | None, tuple[BundleStatus, ...], str, datetime]:
        deps, query, outbox = self.deps, self.deps.link, self.deps.outbox
        if query is None or outbox is None:
            raise CatchUpUnavailable()
        with deps.snapshots.snapshot() as view:
            last_gap = query.last_gap(view)
            gap = (
                None
                if last_gap is None
                else LogGap(last_gap.started_at, last_gap.ended_at, last_gap.gap_kind, last_gap.gap_planned)
            )
            effective = since if since is not None else (None if gap is None else gap.started_at)
            delivered = outbox.delivered(view, since=effective, limit=deps.settings.log_max_items + 1)
            release = view.release
            unknown = parse_words(
                release.document(ConfigName.UI_COPY).raw, release.document(ConfigName.HELP_PRESENTATION).raw
            ).catch.unknown
            return effective, gap, delivered, unknown, view.now

    def _lines(self, bundle: BundleStatus, unknown: str) -> _Lines:
        if bundle.ref_kind != "ground_message":
            # Handoffs carry no catch-up until E16 supplies their facts; their subject is unknown, so they are
            # shown to nobody (fail closed) rather than with invented content.
            return _Lines(unknown, Composition((unknown,), (unknown,), (unknown,), (unknown,)), None)
        with self.deps.snapshots.snapshot() as view:
            row = self.deps.repository.message(view, bundle.ref_id)
            if row is not None and row.state == "frozen":
                return _Lines(
                    row.title, self.deps.repository.lines(view, row.ground_message_id), row.subject_person_id
                )
        if row is None:
            raise CatchUpUnavailable()
        provider = self.deps.help_snapshots
        if provider is None:
            raise CatchUpUnavailable()
        try:
            snapshot = self._snapshot_call.call(lambda: provider.for_message(row.ground_message_id))
        except StorageUnavailable:
            raise
        except CatchUpUnavailable:
            raise
        except Exception as error:
            raise CatchUpUnavailable() from error
        if snapshot is None or (snapshot.help_session_id, snapshot.session_revision) != (
            row.help_session_id,
            row.session_revision,
        ):
            raise CatchUpUnavailable()
        return _Lines(row.title, self.composer.compose(snapshot)[0], snapshot.subject_person_id)
