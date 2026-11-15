from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from crew_health.episodes.application.codec import array, instant, integer, text
from crew_health.episodes.domain.onset import Onset
from crew_health.episodes.domain.types import Episode, EpisodeState, LinkFact, TaskFact
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork


class EpisodeRepository:
    def __init__(self, *, max_links: int = 1000, max_tasks: int = 50) -> None:
        self.max_links, self.max_tasks = max_links, max_tasks

    def get(self, view: View, ref: str) -> Episode | None:
        rows = view.connection.rows("SELECT * FROM episodes_episode WHERE episode_id=?", (ref,), limit=1)
        if not rows:
            return None
        r = rows[0]
        flags: object = json.loads(cast(bytes, r[12]))
        return Episode(
            text(r[0]),
            text(r[1]),
            text(r[2]),
            text(r[3]),
            text(r[4]),
            Onset(
                datetime.fromisoformat(text(r[5])) if r[5] is not None else None,
                cast(Literal["exact", "hour", "day", "unknown"], text(r[6])),
            ),
            EpisodeState(text(r[7])),
            integer(r[8]),
            datetime.fromisoformat(text(r[9])),
            Actor("person", text(r[10])),
            text(r[11]),
            tuple(text(v) for v in array(flags)),
            text(r[13]) if r[13] is not None else None,
            text(r[14]),
            text(r[15]) if r[15] is not None else None,
        )

    def require(self, view: View, ref: str) -> Episode:
        ep = self.get(view, ref)
        if ep is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "episode not found")
        return ep

    def create(self, uow: UnitOfWork, ep: Episode) -> None:
        uow.connection.execute(
            "INSERT INTO episodes_episode VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ep.episode_id,
                ep.patient_id,
                ep.mission_id,
                ep.origin_mode,
                ep.concern_text,
                instant(ep.onset.at) if ep.onset.at else None,
                ep.onset.precision,
                ep.state.value,
                ep.revision,
                instant(ep.created_at),
                ep.created_by.id,
                ep.questionnaire_version,
                canonical_json(ep.red_flag_ids),
                ep.route_module_id,
                ep.config_release_id,
                None,
            ),
        )
        self.onset(uow, ep)
        self.change(uow, ep, ep.state, "created", revision=1)

    def onset(self, uow: UnitOfWork, ep: Episode) -> None:
        rows = uow.connection.rows(
            "SELECT coalesce(max(seq),0)+1 FROM episodes_onset WHERE episode_id=?", (ep.episode_id,), limit=1
        )
        uow.connection.execute(
            "INSERT INTO episodes_onset VALUES (?,?,?)",
            (
                ep.episode_id,
                integer(rows[0][0]),
                canonical_json(
                    {
                        "onset": {
                            "at": instant(ep.onset.at) if ep.onset.at else None,
                            "precision": ep.onset.precision,
                        },
                        "recorded_at": instant(uow.now),
                        "by": {"kind": uow.actor.kind, "id": uow.actor.id},
                    }
                ),
            ),
        )

    def change(
        self,
        uow: UnitOfWork,
        ep: Episode,
        state: EpisodeState,
        kind: str,
        *,
        cause: str | None = None,
        revision: int | None = None,
    ) -> Episode:
        rev = revision if revision is not None else ep.revision + 1
        uow.connection.execute(
            "INSERT INTO episodes_change VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ep.episode_id,
                rev,
                kind,
                uow.actor.kind,
                uow.actor.id,
                instant(uow.now),
                ep.state.value,
                state.value,
                cause,
            ),
        )
        updated = replace(ep, state=state, revision=rev)
        if revision is None:
            uow.connection.execute(
                "UPDATE episodes_episode SET state=?,revision=?,onset_at=?,onset_precision=?,route_module_id=?,last_resolution_id=? WHERE episode_id=? AND revision=?",
                (
                    state.value,
                    rev,
                    instant(ep.onset.at) if ep.onset.at else None,
                    ep.onset.precision,
                    ep.route_module_id,
                    ep.last_resolution_id,
                    ep.episode_id,
                    ep.revision,
                ),
            )
        return updated

    def links(self, view: View, episode_id: str) -> tuple[tuple[SqlValue, ...], ...]:
        return view.connection.rows(
            "SELECT kind,ref_id,ref_state FROM episodes_link WHERE episode_id=? ORDER BY kind,ref_id",
            (episode_id,),
            limit=self.max_links,
        )

    def task_facts(self, view: View, episode_id: str) -> tuple[TaskFact, ...]:
        return tuple(
            TaskFact(text(r[0]), text(r[1]))
            for r in view.connection.rows(
                "SELECT kind,state FROM episodes_task WHERE episode_id=? AND state=?",
                (episode_id, "open"),
                limit=self.max_tasks,
            )
        )

    def link_facts(self, view: View, episode_id: str) -> tuple[LinkFact, ...]:
        return tuple(
            LinkFact(text(r[0]), text(r[2]) if r[2] is not None else None)
            for r in self.links(view, episode_id)
        )

    def timeline(
        self,
        uow: UnitOfWork,
        ep: Episode,
        *,
        entry_id: str,
        kind: str,
        ref_id: str,
        state: str | None = None,
        occurred_at: datetime | None = None,
        dedupe: str | None = None,
        source: str = "episode",
    ) -> bool:
        key = dedupe or entry_id
        if uow.connection.rows("SELECT 1 FROM episodes_timeline WHERE dedupe_key=?", (key,), limit=1):
            return False
        rows = uow.connection.rows(
            "SELECT coalesce(max(seq),0)+1 FROM episodes_timeline WHERE episode_id=?",
            (ep.episode_id,),
            limit=1,
        )
        uow.connection.execute(
            "INSERT INTO episodes_timeline VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                ep.episode_id,
                integer(rows[0][0]),
                kind,
                ref_id,
                state,
                instant(occurred_at or uow.now),
                instant(uow.now),
                uow.actor.kind,
                uow.actor.id,
                source,
                key,
            ),
        )
        return True
