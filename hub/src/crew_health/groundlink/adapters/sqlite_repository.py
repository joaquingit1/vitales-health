"""SYSTEM 5.7: bounded SQL through the pinned storage session."""

from dataclasses import asdict
from datetime import datetime
from typing import Literal, cast

from crew_health.contracts_gen.schemas.ground_link_schema import GroundLink
from crew_health.contracts_gen.schemas.requests_schema import GroundLinkDemoSwitch2
from crew_health.groundlink.application.ports import GapKind, GroundLinkView, InboundItem, LinkGap, RefKind
from crew_health.groundlink.application.repository import Ack, QuarantineRecord, StatusRecord
from crew_health.groundlink.application.settings import GroundLinkSettings
from crew_health.groundlink.application.wire import link_wire
from crew_health.groundlink.domain.link import DemoOverride
from crew_health.groundlink.domain.queue import Bundle
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork

_BUNDLE_COLUMNS = (
    "bundle_id,ref_kind,ref_id,content_sha256,queued_at,state,attempts,next_attempt_at,"
    "sent_at,arrival_due_at,arrived_at,one_way_delay_s,simulated,last_error"
)
_ACK_COLUMNS = (
    "ack_id,event_id,ref_kind,ref_id,kind,sent_by_ground_at,text,by_kind,by_id,state,"
    "arrival_due_at,delivered_at,attempts,next_attempt_at,last_error"
)


def _stamp(value: datetime | None) -> str | None:
    # Fixed precision preserves chronological ordering in SQLite TEXT indexes.
    return None if value is None else value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid stored text")
    return value


def _time(value: SqlValue) -> datetime:
    return datetime.fromisoformat(_text(value))


def _optional_time(value: SqlValue) -> datetime | None:
    return None if value is None else _time(value)


def _integer(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise ValueError("invalid stored integer")
    return value


def _link(raw: SqlValue) -> GroundLinkView:
    p = GroundLink.model_validate_json(_text(raw))
    return GroundLinkView(
        p.status,
        None if p.one_way_delay_s is None else p.one_way_delay_s.root,
        None if p.next_contact_at is None else p.next_contact_at.root,
        None if p.last_contact_at is None else p.last_contact_at.root,
        p.source,
        p.environment_id.root,
        p.delay_text,
        tuple(s.root for s in p.delay_source_ids.root),
        None if p.gap_kind is None else p.gap_kind.root,
        p.gap_planned,
    )


def _bundle(row: tuple[SqlValue, ...]) -> Bundle:
    return Bundle(
        _text(row[0]),
        cast(RefKind, _text(row[1])),
        _text(row[2]),
        _text(row[3]),
        _time(row[4]),
        cast(Literal["queued", "sending", "sent", "arrived"], _text(row[5])),
        _integer(row[6]),
        _optional_time(row[7]),
        _optional_time(row[8]),
        _optional_time(row[9]),
        _optional_time(row[10]),
        None if row[11] is None else float(_text(row[11])),
        bool(row[12]),
        None if row[13] is None else _text(row[13]),
    )


def _bundle_values(b: Bundle) -> tuple[SqlValue, ...]:
    return (
        b.bundle_id,
        b.ref_kind,
        b.ref_id,
        b.content_sha256,
        _stamp(b.queued_at),
        b.state,
        b.attempts,
        _stamp(b.next_attempt_at),
        _stamp(b.sent_at),
        _stamp(b.arrival_due_at),
        _stamp(b.arrived_at),
        None if b.one_way_delay_s is None else str(b.one_way_delay_s),
        int(b.simulated),
        b.last_error,
    )


def _ack(row: tuple[SqlValue, ...]) -> Ack:
    actor = Actor(
        cast(
            Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"],
            _text(row[7]),
        ),
        _text(row[8]),
    )
    item = InboundItem(
        _text(row[1]),
        cast(RefKind, _text(row[2])),
        _text(row[3]),
        cast(Literal["receipt", "reply"], _text(row[4])),
        _time(row[5]),
        None if row[6] is None else _text(row[6]),
        actor,
    )
    return Ack(
        _text(row[0]),
        item,
        cast(Literal["waiting", "due", "delivered"], _text(row[9])),
        _optional_time(row[10]),
        _optional_time(row[11]),
        _integer(row[12]),
        _optional_time(row[13]),
        None if row[14] is None else _text(row[14]),
    )


class SqliteGroundLinkRepository:
    def __init__(self, settings: GroundLinkSettings) -> None:
        self.settings = settings

    def environment(self, view: ReadSnapshot) -> str | None:
        rows = view.connection.rows(
            "SELECT environment_id FROM groundlink_status WHERE kind='environment' ORDER BY seq DESC LIMIT 1",
            (),
            limit=1,
        )
        return None if not rows else _text(rows[0][0])

    def environment_at(self, view: ReadSnapshot) -> datetime | None:
        rows = view.connection.rows(
            "SELECT at FROM groundlink_status WHERE kind='environment' ORDER BY seq DESC LIMIT 1",
            (),
            limit=1,
        )
        return None if not rows else _time(rows[0][0])

    def override(self, view: ReadSnapshot) -> DemoOverride | None:
        rows = view.connection.rows(
            "SELECT override_json,at FROM groundlink_status WHERE kind IN ('environment','override','clear') ORDER BY seq DESC LIMIT 1",
            (),
            limit=1,
        )
        if not rows or rows[0][0] is None:
            return None
        p = GroundLinkDemoSwitch2.model_validate_json(_text(rows[0][0]))
        return DemoOverride(
            p.status,
            None if p.one_way_delay_s is None else p.one_way_delay_s.root,
            None if p.next_contact_at is None else p.next_contact_at.root,
            None if p.gap_kind is None else p.gap_kind.root,
            bool(p.gap_planned),
            _time(rows[0][1]),
        )

    def history(self, view: ReadSnapshot) -> tuple[StatusRecord, ...]:
        bound = self.settings.history_rows
        rows = view.connection.rows(
            "SELECT at,state_json FROM groundlink_status ORDER BY seq DESC LIMIT ?", (bound,), limit=bound
        )
        return tuple(StatusRecord(_time(row[0]), _link(row[1])) for row in reversed(rows))

    def last_gap(self, view: ReadSnapshot) -> LinkGap | None:
        rows = view.connection.rows(
            "SELECT started_at,ended_at,gap_kind,gap_planned FROM groundlink_gap ORDER BY gap_seq DESC LIMIT 1",
            (),
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return LinkGap(_time(row[0]), _optional_time(row[1]), cast(GapKind, _text(row[2])), bool(row[3]))

    def append(
        self,
        uow: UnitOfWork,
        kind: Literal["environment", "override", "clear", "observed"],
        at: datetime,
        link: GroundLinkView,
        override: DemoOverride | None = None,
    ) -> int:
        data = None
        if override is not None:
            body = asdict(override)
            del body["set_at"]
            body["next_contact_at"] = _stamp(override.next_contact_at)
            if body["gap_kind"] is None:
                del body["gap_kind"]
            data = canonical_json(body).decode()
        result = uow.connection.execute(
            "INSERT INTO groundlink_status(kind,at,environment_id,override_json,state_json) VALUES(?,?,?,?,?)",
            (kind, _stamp(at), link.environment_id, data, canonical_json(link_wire(link)).decode()),
        )
        if result.last_row_id is None:
            raise ValueError("history row has no sequence")
        seq = result.last_row_id
        self._append_gap(uow, seq, at, link)
        self._prune(uow, seq)
        return seq

    def _append_gap(self, uow: UnitOfWork, seq: int, at: datetime, link: GroundLinkView) -> None:
        rows = uow.connection.rows(
            "SELECT gap_seq,ended_at FROM groundlink_gap ORDER BY gap_seq DESC LIMIT 1", (), limit=1
        )
        open_gap = bool(rows) and rows[0][1] is None
        if link.status == "down" and not open_gap:
            if link.gap_kind is None:
                raise ValueError("down state lacks gap_kind")
            uow.connection.execute(
                "INSERT INTO groundlink_gap(gap_seq,started_at,gap_kind,gap_planned) VALUES(?,?,?,?)",
                (seq, _stamp(at), link.gap_kind, int(link.gap_planned)),
            )
        elif link.status != "down" and open_gap:
            uow.connection.execute(
                "UPDATE groundlink_gap SET ended_at=? WHERE gap_seq=?", (_stamp(at), rows[0][0])
            )

    def _prune(self, uow: UnitOfWork, seq: int) -> None:
        retention = self.settings.history_retention_rows
        uow.connection.execute(
            "INSERT INTO groundlink_retention VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET retention_rows=excluded.retention_rows",
            (retention,),
        )
        uow.connection.execute(
            """DELETE FROM groundlink_status WHERE seq<=?
            AND seq != coalesce((SELECT max(seq) FROM groundlink_status WHERE kind='environment'),0)
            AND seq != coalesce((SELECT max(seq) FROM groundlink_status WHERE kind IN ('environment','override','clear')),0)""",
            (seq - retention,),
        )
        uow.connection.execute(
            "DELETE FROM groundlink_gap WHERE gap_seq < (SELECT gap_seq FROM groundlink_gap ORDER BY gap_seq DESC LIMIT 1 OFFSET ?)",
            (retention - 1,),
        )

    def quarantine(self, uow: UnitOfWork, record: QuarantineRecord) -> None:
        uow.connection.execute(
            "INSERT INTO groundlink_quarantine VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            (
                record.event_id,
                record.subscriber,
                record.event_type,
                record.reason,
                record.attempts,
                _stamp(record.quarantined_at),
            ),
        )

    def quarantined(self, view: ReadSnapshot, limit: int) -> tuple[QuarantineRecord, ...]:
        if not 1 <= limit <= self.settings.renewal_batch:
            raise ValueError("quarantine read exceeds row bound")
        rows = view.connection.rows(
            "SELECT event_id,subscriber,event_type,reason,attempts,quarantined_at FROM groundlink_quarantine ORDER BY quarantined_at,event_id LIMIT ?",
            (limit,),
            limit=limit,
        )
        return tuple(
            QuarantineRecord(_text(r[0]), _text(r[1]), _text(r[2]), _text(r[3]), _integer(r[4]), _time(r[5]))
            for r in rows
        )

    def recorded(self, view: ReadSnapshot, seq: int) -> GroundLinkView | None:
        rows = view.connection.rows("SELECT state_json FROM groundlink_status WHERE seq=?", (seq,), limit=1)
        return _link(rows[0][0]) if rows else None

    def last_contact(self, view: ReadSnapshot) -> datetime | None:
        rows = view.connection.rows(
            "SELECT max(at) FROM (SELECT max(sent_at) at FROM groundlink_bundle UNION ALL SELECT max(arrived_at) FROM groundlink_bundle UNION ALL SELECT max(delivered_at) FROM groundlink_ack)",
            (),
            limit=1,
        )
        return _optional_time(rows[0][0])

    def bundle(self, view: ReadSnapshot, ref_kind: RefKind, ref_id: str) -> Bundle | None:
        rows = view.connection.rows(
            "SELECT " + _BUNDLE_COLUMNS + " FROM groundlink_bundle WHERE ref_kind=? AND ref_id=?",
            (ref_kind, ref_id),
            limit=1,
        )
        return None if not rows else _bundle(rows[0])

    def insert_bundle(self, uow: UnitOfWork, bundle: Bundle) -> None:
        uow.connection.execute(
            "INSERT INTO groundlink_bundle(" + _BUNDLE_COLUMNS + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _bundle_values(bundle),
        )

    def save_bundle(self, uow: UnitOfWork, bundle: Bundle) -> None:
        values = _bundle_values(bundle)
        uow.connection.execute(
            "UPDATE groundlink_bundle SET state=?,attempts=?,next_attempt_at=?,sent_at=?,arrival_due_at=?,arrived_at=?,one_way_delay_s=?,simulated=?,last_error=? WHERE bundle_id=?",
            values[5:] + (bundle.bundle_id,),
        )

    def send_candidates(self, view: ReadSnapshot) -> tuple[Bundle, ...]:
        bound = self.settings.max_inflight
        rows = view.connection.rows(
            "SELECT "
            + _BUNDLE_COLUMNS
            + " FROM groundlink_bundle WHERE state IN ('queued','sending') ORDER BY queued_at,bundle_id LIMIT ?",
            (bound,),
            limit=bound,
        )
        return tuple(_bundle(row) for row in rows)

    def arrival_candidates(self, view: ReadSnapshot) -> tuple[Bundle, ...]:
        bound = self.settings.renewal_batch
        rows = view.connection.rows(
            "SELECT "
            + _BUNDLE_COLUMNS
            + " FROM groundlink_bundle WHERE state='sent' AND arrival_due_at<=? AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY arrival_due_at,bundle_id LIMIT ?",
            (_stamp(view.now), _stamp(view.now), bound),
            limit=bound,
        )
        return tuple(_bundle(row) for row in rows)

    def delivered(self, view: ReadSnapshot, since: datetime | None, limit: int) -> tuple[Bundle, ...]:
        bound = limit
        rows = view.connection.rows(
            "SELECT "
            + _BUNDLE_COLUMNS
            + " FROM groundlink_bundle WHERE state='arrived' AND (? IS NULL OR arrived_at>=?) ORDER BY arrived_at,ref_id LIMIT ?",
            (_stamp(since), _stamp(since), bound),
            limit=bound,
        )
        return tuple(_bundle(row) for row in rows)

    def ack_exists(self, view: ReadSnapshot, event_id: str) -> bool:
        return bool(
            view.connection.rows("SELECT ack_id FROM groundlink_ack WHERE event_id=?", (event_id,), limit=1)
        )

    def insert_ack(self, uow: UnitOfWork, ack: Ack) -> None:
        i = ack.item
        uow.connection.execute(
            "INSERT INTO groundlink_ack(" + _ACK_COLUMNS + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ack.ack_id,
                i.event_id,
                i.ref_kind,
                i.ref_id,
                i.kind,
                _stamp(i.sent_by_ground_at),
                i.text,
                i.by.kind,
                i.by.id,
                ack.state,
                _stamp(ack.arrival_due_at),
                _stamp(ack.delivered_at),
                ack.attempts,
                _stamp(ack.next_attempt_at),
                ack.last_error,
            ),
        )

    def save_ack(self, uow: UnitOfWork, ack: Ack) -> None:
        uow.connection.execute(
            "UPDATE groundlink_ack SET state=?,arrival_due_at=?,delivered_at=?,attempts=?,next_attempt_at=?,last_error=? WHERE ack_id=?",
            (
                ack.state,
                _stamp(ack.arrival_due_at),
                _stamp(ack.delivered_at),
                ack.attempts,
                _stamp(ack.next_attempt_at),
                ack.last_error,
                ack.ack_id,
            ),
        )

    def inbound_candidates(self, view: ReadSnapshot) -> tuple[Ack, ...]:
        bound = self.settings.renewal_batch
        rows = view.connection.rows(
            "SELECT "
            + _ACK_COLUMNS
            + " FROM groundlink_ack WHERE state!='delivered' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY coalesce(arrival_due_at,sent_by_ground_at),ack_id LIMIT ?",
            (_stamp(view.now), bound),
            limit=bound,
        )
        return tuple(_ack(row) for row in rows)
