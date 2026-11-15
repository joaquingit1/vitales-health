"""reports_confirmation and reports_finding: the hub-side clinical lineage of confirmed findings (E09).

Spans, source text, names and hashes stay encrypted in the private version; this table keeps what a confirmed
finding is (code, value, unit, date, origin, attribution) and where it came from (ids and revisions)."""

from __future__ import annotations

import json
from datetime import date as Day
from datetime import datetime
from decimal import Decimal
from typing import cast

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.reports.domain.model import (
    ConfirmedFinding,
    FindingOrigin,
    FindingRole,
    OriginMode,
    origin_mode_of,
)

type View = ReadSnapshot | UnitOfWork

_FINDING_COLUMNS = (
    "finding_id,report_id,draft_id,patient_id,code,value_kind,value,unit,observed_on,origin,role,origin_mode,"
    "confirmer_id,confirmed_at,report_revision,source_version_id,attributes,reference_low,reference_high,"
    "record_ref"
)


def _origin(raw: object) -> FindingOrigin:
    if raw == "clinician":
        return "clinician"
    if raw == "device":
        return "device"
    if raw == "extraction":
        return "extraction"
    raise StorageUnavailable("corrupt")


def _role(raw: object) -> FindingRole:
    if raw == "result":
        return "result"
    if raw == "interpretation":
        return "interpretation"
    raise StorageUnavailable("corrupt")


def _finding(raw: tuple[SqlValue, ...]) -> ConfirmedFinding:
    try:
        attributes = json.loads(cast(str, raw[16]))
        low, high = raw[17], raw[18]
        mode: OriginMode = origin_mode_of(str(raw[11]))
        return ConfirmedFinding(
            str(raw[0]),
            None if raw[1] is None else str(raw[1]),
            None if raw[2] is None else str(raw[2]),
            str(raw[3]),
            str(raw[4]),
            Decimal(str(raw[6])) if raw[5] == "number" else str(raw[6]),
            None if raw[7] is None else str(raw[7]),
            None if raw[8] is None else Day.fromisoformat(str(raw[8])),
            None,
            _origin(raw[9]),
            _role(raw[10]),
            mode,
            str(raw[12]),
            datetime.fromisoformat(str(raw[13])),
            None if raw[14] is None else int(str(raw[14])),
            None if raw[15] is None else str(raw[15]),
            tuple((str(k), str(v)) for k, v in attributes),
            None if low is None or high is None else (Decimal(str(low)), Decimal(str(high))),
            None if raw[19] is None else str(raw[19]),
        )
    except (ValueError, TypeError, IndexError, ArithmeticError) as error:
        raise StorageUnavailable("corrupt") from error


class SqliteConfirmationLineage:
    def advance(self, uow: UnitOfWork, *, report_id: str, expected_revision: int, new_revision: int) -> bool:
        """Compare and set the hub revision. The private version attaches in the same transaction."""
        result = uow.connection.execute(
            "UPDATE reports_report SET revision=? WHERE report_id=? AND revision=? AND deleted=0",
            (new_revision, report_id, expected_revision),
        )
        return result.affected_rows == 1

    def bind(self, uow: UnitOfWork, *, command_key: str, report_id: str, request_digest: str) -> None:
        uow.connection.execute(
            "INSERT INTO reports_confirmation(owner_person_id,command_key,report_id,request_digest) "
            "VALUES (?,?,?,?)",
            (uow.actor.id, command_key, report_id, request_digest),
        )

    def binding(self, view: View, *, owner_person_id: str, command_key: str) -> tuple[str, str] | None:
        rows = view.connection.rows(
            "SELECT report_id,request_digest FROM reports_confirmation WHERE owner_person_id=? AND command_key=?",
            (owner_person_id, command_key),
            limit=1,
        )
        if not rows:
            return None
        return ("" if rows[0][0] is None else str(rows[0][0]), str(rows[0][1]))

    def applied(self, view: View, *, command_key: str) -> bool:
        rows = view.connection.rows(
            "SELECT 1 FROM reports_confirmation WHERE command_key=? AND applied=1", (command_key,), limit=1
        )
        return bool(rows)

    def append(self, uow: UnitOfWork, findings: tuple[ConfirmedFinding, ...], *, command_key: str) -> None:
        owner = uow.actor.id
        uow.connection.execute(
            "INSERT INTO reports_confirmation(owner_person_id,command_key,report_id,request_digest,applied) "
            "VALUES (?,?,NULL,'',1) ON CONFLICT(owner_person_id,command_key) DO UPDATE SET applied=1",
            (owner, command_key),
        )
        for seq, f in enumerate(findings):
            uow.connection.execute(
                f"INSERT INTO reports_finding(owner_person_id,command_key,seq,{_FINDING_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    owner,
                    command_key,
                    seq,
                    f.finding_id,
                    f.report_id,
                    f.draft_id,
                    f.patient_id,
                    f.code,
                    "number" if isinstance(f.value, Decimal) else "text",
                    str(f.value),
                    f.unit,
                    None if f.observed_on is None else f.observed_on.isoformat(),
                    f.origin,
                    f.role,
                    f.origin_mode,
                    f.confirmer_id,
                    f.confirmed_at.isoformat(),
                    f.report_revision,
                    f.source_version_id,
                    canonical_json([list(pair) for pair in f.attributes]).decode(),
                    None if f.reference_interval is None else str(f.reference_interval[0]),
                    None if f.reference_interval is None else str(f.reference_interval[1]),
                    f.record_ref,
                ),
            )

    def findings_of(self, view: View, *, command_key: str) -> tuple[ConfirmedFinding, ...]:
        rows = view.connection.rows(
            f"SELECT {_FINDING_COLUMNS} FROM reports_finding WHERE command_key=? ORDER BY seq",
            (command_key,),
            limit=1000,
        )
        return tuple(_finding(r) for r in rows)
