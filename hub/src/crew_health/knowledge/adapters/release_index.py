from __future__ import annotations

from collections.abc import Sequence

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.release import ApplicabilityFilter, ReleaseMembership
from crew_health.knowledge.domain.results import IndexUnavailable, SearchMode


def _table(mode: SearchMode) -> str:
    if mode == "exact":
        return "knowledge_fts_exact"
    if mode == "stemmed":
        return "knowledge_fts_stemmed"
    raise EvidenceInvalid("search_mode")


def add_index_batch(
    uow: UnitOfWork,
    memberships: Sequence[ReleaseMembership],
    *,
    settings: HubSettings,
) -> None:
    require_bound(len(memberships), settings.integer("knowledge.admin.batch_size"))
    for member in memberships:
        release_id, passage_id = member.release_id, member.passage.passage_id
        if uow.connection.rows(
            "SELECT release_id FROM knowledge_index_ready WHERE release_id=?", (release_id,), limit=1
        ):
            raise EvidenceInvalid("index_already_ready")
        for mode in ("exact", "stemmed"):
            table = _table(mode)
            existing = uow.connection.rows(
                f"SELECT passage_id FROM {table} WHERE release_id=? AND passage_id=? LIMIT 1",
                (release_id, passage_id),
                limit=1,
            )
            if existing:
                raise EvidenceInvalid("duplicate_index_member")
            result = uow.connection.execute(
                f"INSERT INTO {table}(release_id,passage_id,search_text) VALUES (?,?,?)",
                (release_id, passage_id, member.passage.text),
            )
            if result.affected_rows != 1:
                raise EvidenceInvalid("index_write")
        for status in member.uses:
            if not member.withdrawn and status.status == "approved":
                environments = (
                    None
                    if member.environments is None
                    else canonical_json(sorted(member.environments)).decode("utf-8")
                )
                result = uow.connection.execute(
                    "INSERT INTO knowledge_index_member VALUES (?,?,?,?)",
                    (release_id, passage_id, status.use, environments),
                )
                if result.affected_rows != 1:
                    raise EvidenceInvalid("index_write")


def seal_index(uow: UnitOfWork, release_id: str, expected_count: int, *, settings: HubSettings) -> None:
    require_id(release_id)
    require_bound(expected_count, settings.integer("knowledge.archive.max_passages"))
    for mode in ("exact", "stemmed"):
        rows = uow.connection.rows(
            f"SELECT count(*) FROM {_table(mode)} WHERE release_id=?",
            (release_id,),
            limit=1,
        )
        if rows != ((expected_count,),):
            raise EvidenceInvalid("index_count")
    result = uow.connection.execute(
        "INSERT INTO knowledge_index_ready VALUES (?,?)", (release_id, expected_count)
    )
    if result.affected_rows != 1:
        raise EvidenceInvalid("index_write")


class SQLiteReleaseIndex:
    def __init__(self, view: ReadSnapshot, *, settings: HubSettings) -> None:
        self._view, self._settings = view, settings

    def search(
        self,
        release_id: str,
        query: str,
        use: str,
        filters: ApplicabilityFilter,
        mode: SearchMode,
        k: int,
    ) -> tuple[str, ...] | IndexUnavailable:
        require_bound(len(query), self._settings.integer("knowledge.query.max_chars"))
        require_bound(k, self._settings.integer("knowledge.query.k_max"))
        if not query.strip() or k < 1:
            raise EvidenceInvalid("query")
        if filters.environments is not None:
            require_bound(len(filters.environments), self._settings.integer("knowledge.query.max_source_ids"))
        table = _table(mode)
        required = (table, "knowledge_index_member", "knowledge_index_ready")
        names = self._view.connection.rows(
            "SELECT name FROM sqlite_master WHERE name IN (?,?,?) ORDER BY name",
            required,
            limit=len(required),
        )
        if len(names) != len(required):
            return IndexUnavailable(release_id, "lexical", "index_unavailable")
        ready = self._view.connection.rows(
            "SELECT passage_count FROM knowledge_index_ready WHERE release_id=?", (release_id,), limit=1
        )
        if not ready:
            return IndexUnavailable(release_id, "lexical", "index_unavailable")
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in query.split())
        parameters: tuple[SqlValue, ...] = (
            expression,
            release_id,
            use,
            int(filters.allow_unknown),
            None
            if filters.environments is None
            else canonical_json(sorted(filters.environments)).decode("utf-8"),
            k,
        )
        rows = self._view.connection.rows(
            f"SELECT f.passage_id FROM {table} f JOIN knowledge_index_member m "
            "ON m.release_id=f.release_id AND m.passage_id=f.passage_id "
            f"WHERE {table} MATCH ? AND f.release_id=? AND m.use=? "
            "AND ((m.environments IS NULL AND ?=1) OR (m.environments IS NOT NULL AND "
            "(?5 IS NULL OR EXISTS (SELECT 1 FROM json_each(m.environments) a JOIN json_each(?5) b ON a.value=b.value)))) "
            f"ORDER BY bm25({table}),f.passage_id LIMIT ?6",
            parameters,
            limit=k,
        )
        if any(len(row) != 1 or not isinstance(row[0], str) for row in rows):
            raise EvidenceInvalid("index_member")
        return tuple(row[0] for row in rows if isinstance(row[0], str))
