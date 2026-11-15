"""Hub-side index of private report versions: opaque refs only (SYSTEM 5.8.1). Rows hold ids, the sealed
reference fields and a revision; never a name, hash of content, date, draft or value."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.private import SealedPrivateRef
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork

RECORD_KIND = "report"
_COLUMNS = (
    "v.entry_id,v.report_id,v.revision,v.version_id,v.owner_person_id,v.command_key,v.fingerprint,v.key_id,"
    "v.ciphertext_sha256,v.deleted OR r.deleted,r.source_entry_id"
)


@dataclass(frozen=True, slots=True)
class VersionRow:
    entry_id: str
    report_id: str
    revision: int
    version_id: str
    owner_person_id: str
    command_key: str
    fingerprint: str
    key_id: str
    ciphertext_sha256: str
    deleted: bool
    source_entry_id: str

    def reference(self) -> SealedPrivateRef:
        return SealedPrivateRef(
            self.entry_id,
            self.version_id,
            RECORD_KIND,
            self.owner_person_id,
            self.command_key,
            self.fingerprint,
            self.key_id,
            self.ciphertext_sha256,
        )


def _row(raw: tuple[object, ...]) -> VersionRow:
    try:
        return VersionRow(
            str(raw[0]),
            str(raw[1]),
            int(str(raw[2])),
            str(raw[3]),
            str(raw[4]),
            str(raw[5]),
            str(raw[6]),
            str(raw[7]),
            str(raw[8]),
            bool(raw[9]),
            str(raw[10]),
        )
    except (ValueError, IndexError) as error:
        raise StorageUnavailable("corrupt") from error


class ReportIndex:
    def current(self, view: View, report_id: str) -> VersionRow | None:
        rows = view.connection.rows(
            f"SELECT {_COLUMNS} FROM reports_report r JOIN reports_version v ON v.entry_id=r.current_entry_id "
            "WHERE r.report_id=?",
            (report_id,),
            limit=1,
        )
        return _row(rows[0]) if rows else None

    def source(self, view: View, report_id: str) -> VersionRow | None:
        rows = view.connection.rows(
            f"SELECT {_COLUMNS} FROM reports_report r JOIN reports_version v ON v.entry_id=r.source_entry_id "
            "WHERE r.report_id=?",
            (report_id,),
            limit=1,
        )
        return _row(rows[0]) if rows else None

    def by_command(self, view: View, owner_person_id: str, command_key: str) -> VersionRow | None:
        rows = view.connection.rows(
            f"SELECT {_COLUMNS} FROM reports_version v JOIN reports_report r ON r.report_id=v.report_id "
            "WHERE v.owner_person_id=? AND v.command_key=?",
            (owner_person_id, command_key),
            limit=1,
        )
        return _row(rows[0]) if rows else None

    def entries(self, view: View, report_id: str, *, limit: int) -> tuple[str, ...]:
        rows = view.connection.rows(
            "SELECT entry_id FROM reports_version WHERE report_id=? ORDER BY revision",
            (report_id,),
            limit=limit,
        )
        return tuple(str(r[0]) for r in rows)

    def owner(self, view: View, report_id: str) -> str | None:
        rows = view.connection.rows(
            "SELECT owner_person_id FROM reports_report WHERE report_id=? AND deleted=0",
            (report_id,),
            limit=1,
        )
        return str(rows[0][0]) if rows else None
