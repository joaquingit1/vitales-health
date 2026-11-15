from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from crew_health.episodes.application.codec import decode, instant, obj, text
from crew_health.episodes.application.dto import EpisodeFact, EpisodeRef, EpisodeToolFacts, NewEpisode, Viewer
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.episodes.domain.types import MR_PENDING, PLAN_RUN_OPEN, Episode, EpisodeState
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, ReadSnapshot, ReadSnapshotFactory, Role, SessionContext, UnitOfWork

type View = ReadSnapshot | UnitOfWork


class EpisodeQueries:
    def __init__(self, service: EpisodeService, snapshots: ReadSnapshotFactory) -> None:
        self.service, self.repo, self.snapshots = service, service.repo, snapshots

    def get(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None:
        ep = self.repo.get(uow, episode_id)
        return fact(ep) if ep else None

    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool:
        ep = self.repo.get(uow, episode_id)
        # Actor alone carries no role/consent grant. Only the owner can be authorized by this narrower seam.
        return ep is not None and actor.kind == "person" and actor.id == ep.patient_id

    def episode_of(self, uow: UnitOfWork, kind: str, ref_id: str) -> str | None:
        if kind == "red_flag":
            return ref_id if self.repo.get(uow, ref_id) is not None else None
        rows = uow.connection.rows(
            "SELECT episode_id FROM episodes_link WHERE kind=? AND ref_id=?", (kind, ref_id), limit=1
        )
        return text(rows[0][0]) if rows else None

    def view(self, view: View, viewer: Viewer, episode_id: str) -> dict[str, object]:
        if viewer.actor is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "trusted viewer actor required")
        session = SessionContext(
            "session_projection",
            cast(Literal["crew", "station", "ground", "operator", "editor", "adapter"], viewer.kind),
            viewer.actor,
            viewer.person_id,
            frozenset(Role(r) for r in viewer.roles),
        )
        return self.for_session(view, session, episode_id)

    def for_session(self, view: View, session: SessionContext, episode_id: str) -> dict[str, object]:
        ep = self.repo.require(view, episode_id)
        access = self.service.authorize(view, session, ep)
        p = self.service.providers
        if p.answers is None or p.urgency is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "episode linked facts unavailable", retryable=True)
        answers = tuple(p.answers.for_episode(view, episode_id))
        if len(answers) > self.service.limits.max_answers_in_view:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "answer view bound", retryable=True)
        shown = []
        withheld = 0
        for answer in answers:
            if answer.patient_id != ep.patient_id or answer.episode_id != episode_id:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "answer scope mismatch", retryable=False)
            restricted = access != "full" and (
                p.sensitivity is None
                or p.sensitivity.is_restricted(answer.questionnaire_version, answer.question_id)
            )
            allowed = not restricted or (
                p.consent is not None
                and p.consent.allows(
                    view,
                    ep.patient_id,
                    session.person_id or "ground",
                    ("restricted_answer", answer.question_id),
                )
            )
            if not allowed:
                withheld += 1
                continue
            shown.append(
                {
                    "answer_id": answer.answer_id,
                    "question_id": answer.question_id,
                    "questionnaire_version": answer.questionnaire_version,
                    "presence": answer.presence,
                    "value": answer.value,
                    "unit": None,
                    "body_region": None,
                    "laterality": None,
                    "actor": {"kind": "person", "id": ep.patient_id, "display_name": ep.patient_id},
                    "answered_at": instant(answer.answered_at),
                    "origin_mode": answer.origin_mode,
                    "supersedes": answer.supersedes,
                }
            )
        urgency = p.urgency.summary(view, episode_id)
        state = getattr(urgency, "state", urgency)
        if not isinstance(state, str) or state not in (
            "none",
            "needs_urgent_review",
            "active_response",
            "disposition_recorded",
        ):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "urgency unavailable", retryable=True)
        links = self.repo.links(view, episode_id)
        groups: dict[str, list[str]] = {}
        for row in links:
            groups.setdefault(text(row[0]), []).append(text(row[1]))
        tasks = self.tasks(view, episode_id, session)
        result: dict[str, object] = {
            "episode_id": episode_id,
            "revision": ep.revision,
            "patient_id": ep.patient_id,
            "origin_mode": ep.origin_mode,
            "concern_text": ep.concern_text,
            "onset": {"at": instant(ep.onset.at) if ep.onset.at else None, "precision": ep.onset.precision},
            "state": ep.state.value,
            "urgency_state": state,
            "answers": shown,
            "measurement_request_ids": groups.get("measurement_request", []),
            "capability_gaps": [],
            "assessment_ids": groups.get("assessment", []),
            "help_session_ids": groups.get("help_session", []),
            "followup_tasks": tasks,
            "created_at": instant(ep.created_at),
            "allowed_actions": self.actions(view, session, ep, state),
            "withheld_answer_count": withheld,
            "config_release_id": ep.config_release_id,
            "unavailable_parts": [],
        }
        if groups.get("capability_gap"):
            if p.gaps is None:
                raise ProblemError(
                    ErrorCode.INTERNAL_ERROR, "capability gap reader unavailable", retryable=True
                )
            gaps = tuple(p.gaps.for_episode(view, episode_id))
            if len(gaps) > self.service.limits.max_links_per_episode:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "gap view bound", retryable=True)
            result["capability_gaps"] = [dict(gap) for gap in gaps]
        result["acknowledgments"] = self.history(view, "ack", episode_id)
        result["review_ids"] = [
            text(r[0])
            for r in view.connection.rows(
                "SELECT review_id FROM episodes_review WHERE episode_id=? ORDER BY signed_at,review_id",
                (episode_id,),
                limit=self.service.limits.max_links_per_episode,
            )
        ]
        result["resolution"] = (
            self.history(view, "resolution", episode_id)[-1] if ep.last_resolution_id else None
        )
        result["reopenings"] = self.history(view, "reopen", episode_id)
        result["onset_history"] = [
            decode(r[0])
            for r in view.connection.rows(
                "SELECT data FROM episodes_onset WHERE episode_id=? ORDER BY seq",
                (episode_id,),
                limit=self.service.limits.max_links_per_episode,
            )
        ]
        return result

    def actions(self, view: View, session: SessionContext, ep: Episode, urgency_state: str) -> list[str]:
        if session.kind != "crew" or session.actor.kind != "person":
            return []
        keys = ["add_answer"]
        if ep.state != EpisodeState.RESOLVED:
            keys.extend(["request_review", "add_followup"])
        if session.person_id == ep.patient_id:
            keys.append("correct_onset")
        if session.person_id != ep.patient_id and "crew_medical_officer" in session.roles:
            keys.extend(["acknowledge", "record_review"])
        if ep.state == EpisodeState.RESOLVED:
            keys.append("reopen")
        elif (
            ep.state != EpisodeState.DRAFT
            and urgency_state in ("none", "disposition_recorded")
            and not self.repo.task_facts(view, ep.episode_id)
        ):
            links = self.repo.link_facts(view, ep.episode_id)
            if not any(
                (link.kind == "plan_run" and link.state in PLAN_RUN_OPEN)
                or (link.kind == "measurement_request" and link.state in MR_PENDING)
                for link in links
            ):
                if session.person_id == ep.patient_id:
                    keys.append("resolve")
                elif view.connection.rows(
                    "SELECT 1 FROM episodes_review WHERE episode_id=? AND reviewer_id=? AND role=? LIMIT 1",
                    (ep.episode_id, session.person_id, "crew_medical_officer"),
                    limit=1,
                ):
                    keys.append("resolve")
        return keys

    def history(self, view: View, kind: str, episode_id: str) -> list[dict[str, object]]:
        table = {"ack": "episodes_ack", "resolution": "episodes_resolution", "reopen": "episodes_reopen"}.get(
            kind
        )
        if table is None:
            raise ValueError(kind)
        return [
            decode(r[0])
            for r in view.connection.rows(
                f"SELECT data FROM {table} WHERE episode_id=? ORDER BY rowid",
                (episode_id,),
                limit=self.service.limits.max_links_per_episode,
            )
        ]

    def tasks(
        self, view: View, episode_id: str, session: SessionContext | None = None
    ) -> list[dict[str, object]]:
        rows = view.connection.rows(
            "SELECT task_id,data FROM episodes_task WHERE episode_id=? ORDER BY task_id",
            (episode_id,),
            limit=self.service.limits.max_links_per_episode,
        )
        result = []
        for row in rows:
            data = decode(row[1])
            ref = text(row[0])
            overdue = view.connection.rows(
                "SELECT 1 FROM episodes_task_overdue WHERE task_id=?", (ref,), limit=1
            )
            data["overdue"] = bool(overdue) and data["state"] == "open"
            data["sources"] = [
                {"kind": text(r[0]), "ref_id": text(r[1])}
                for r in view.connection.rows(
                    "SELECT source_kind,ref_id FROM episodes_task_source WHERE task_id=? ORDER BY source_kind,ref_id",
                    (ref,),
                    limit=self.service.limits.max_links_per_episode,
                )
            ]
            allowed: list[str] = []
            if (
                session is not None
                and session.kind == "crew"
                and session.actor.kind == "person"
                and data["state"] == "open"
            ):
                ep = self.repo.require(view, episode_id)
                is_officer = session.person_id != ep.patient_id and "crew_medical_officer" in session.roles
                owner = text(obj(data["owner"])["id"])
                creator = text(obj(data["created_by"])["id"])
                if data["kind"] != "review" and (session.person_id in (owner, ep.patient_id) or is_officer):
                    allowed.append("complete_followup")
                if is_officer or (data["kind"] == "custom" and session.person_id in (creator, owner)):
                    allowed.append("cancel_followup")
                if is_officer or (data["kind"] == "custom" and session.person_id == owner):
                    allowed.append("reassign_followup")
            data["allowed_actions"] = allowed
            result.append(data)
        return result

    def timeline(
        self, view: View, session: SessionContext, episode_id: str, cursor: str | None, limit: int
    ) -> dict[str, object]:
        ep = self.repo.require(view, episode_id)
        self.service.authorize(view, session, ep)
        if not 1 <= limit <= 200 or (cursor is not None and (not cursor.isdigit() or len(cursor) > 16)):
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid page")
        rows = view.connection.rows(
            "SELECT entry_id,seq,kind,ref_id,ref_state,occurred_at,recorded_at,actor_kind,actor_id,source FROM episodes_timeline WHERE episode_id=? AND seq>? ORDER BY seq LIMIT ?",
            (episode_id, int(cursor or "0"), limit + 1),
            limit=limit + 1,
        )
        # Timeline has references only, no restricted answer text or source body.
        items = [
            {
                "entry_id": r[0],
                "seq": r[1],
                "kind": r[2],
                "ref_id": r[3],
                "state": r[4],
                "occurred_at": r[5],
                "recorded_at": r[6],
                "actor": None if r[7] is None else {"kind": r[7], "id": r[8]},
                "origin_mode": ep.origin_mode,
            }
            for r in rows[:limit]
        ]
        return {
            "episode_id": episode_id,
            "revision": ep.revision,
            "items": items,
            "next_cursor": str(rows[limit - 1][1]) if len(rows) > limit else None,
        }

    def list_episodes(
        self,
        view: View,
        session: SessionContext,
        patient_id: str | None,
        cursor: str | None,
        limit: int,
        states: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if session.kind != "crew" or session.actor.kind != "person":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "crew only")
        is_officer = "crew_medical_officer" in session.roles
        if not is_officer:
            if patient_id is not None and patient_id != session.person_id:
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "patient scope")
            patient_id = session.person_id
        if not 1 <= limit <= 200 or len(states) > 6 or any(s not in set(EpisodeState) for s in states):
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid page")
        clauses = ["episode_id>?"]
        params: list[str | int | bytes | None] = [cursor or ""]
        if patient_id is not None:
            clauses.append("patient_id=?")
            params.append(patient_id)
        if states:
            clauses.append("state IN (" + ",".join("?" for _ in states) + ")")
            params.extend(states)
        params.append(limit + 1)
        rows = view.connection.rows(
            "SELECT episode_id,patient_id,state,origin_mode,created_at,revision,route_module_id FROM episodes_episode WHERE "
            + " AND ".join(clauses)
            + " ORDER BY episode_id LIMIT ?",
            tuple(params),
            limit=limit + 1,
        )
        items = [
            {
                "episode_id": r[0],
                "patient_id": r[1],
                "state": r[2],
                "origin_mode": r[3],
                "created_at": r[4],
                "revision": r[5],
                "title": r[6],
            }
            for r in rows[:limit]
        ]
        return {
            "items": items,
            "page": {
                "next_cursor": rows[limit - 1][0] if len(rows) > limit else None,
                "has_more": len(rows) > limit,
            },
        }

    def tool_facts(self, uow: UnitOfWork, episode_id: str, sections: frozenset[str]) -> EpisodeToolFacts:
        ep = self.repo.require(uow, episode_id)
        if uow.actor.kind != "person" or uow.actor.id != ep.patient_id:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "own patient context only")
        available: dict[str, object] = {
            "concern": ep.concern_text,
            "red_flags": list(ep.red_flag_ids),
            "open_tasks": [t for t in self.tasks(uow, episode_id) if t["state"] == "open"],
        }
        if self.service.providers.answers is not None:
            available["answers"] = [
                {
                    "answer_id": a.answer_id,
                    "question_id": a.question_id,
                    "presence": a.presence,
                    "value": a.value,
                }
                for a in self.service.providers.answers.for_episode(uow, episode_id)
            ]
        return EpisodeToolFacts(
            episode_id,
            ep.patient_id,
            ep.revision,
            {k: v for k, v in available.items() if k in sections},
            frozenset(sections - available.keys()),
        )


class ToolFactsFacade:
    def __init__(self, queries: EpisodeQueries) -> None:
        self.queries = queries

    def facts(self, uow: UnitOfWork, episode_id: str, sections: frozenset[str]) -> EpisodeToolFacts:
        return self.queries.tool_facts(uow, episode_id, sections)

    def subject_of(self, uow: UnitOfWork, episode_id: str) -> str | None:
        ep = self.queries.repo.get(uow, episode_id)
        return ep.patient_id if ep else None


class EpisodeReaderFacade:
    def __init__(self, queries: EpisodeQueries) -> None:
        self.queries = queries

    def subject_of(self, episode_id: str) -> str | None:
        with self.queries.snapshots.snapshot() as view:
            ep = self.queries.repo.get(view, episode_id)
            return ep.patient_id if ep else None

    def visible(self, session: SessionContext, episode_id: str) -> EpisodeRef | None:
        with self.queries.snapshots.snapshot() as view:
            ep = self.queries.repo.get(view, episode_id)
            if ep is None:
                return None
            try:
                self.queries.service.authorize(view, session, ep)
            except ProblemError:
                return None
            return EpisodeRef(ep.episode_id, ep.revision)


class RefCheckFacade:
    def __init__(self, queries: EpisodeQueries) -> None:
        self.queries = queries

    def visible(self, ref_id: str, actor: Actor) -> bool:
        with self.queries.snapshots.snapshot() as view:
            ep = self.queries.repo.get(view, ref_id)
            return ep is not None and actor.kind == "person" and actor.id == ep.patient_id


def fact(ep: Episode) -> EpisodeFact:
    return EpisodeFact(
        episode_id=ep.episode_id,
        patient_id=ep.patient_id,
        mission_id=ep.mission_id,
        origin_mode=cast(Literal["LIVE", "REPLAY", "SIM"], ep.origin_mode),
        state=ep.state.value,
        revision=ep.revision,
        questionnaire_version=ep.questionnaire_version,
        red_flag_ids=ep.red_flag_ids,
        route_module_id=ep.route_module_id,
        created_at=ep.created_at,
    )


class KernelFacade:
    def __init__(self, service: EpisodeService, queries: EpisodeQueries) -> None:
        self.service, self.queries = service, queries

    def create(self, uow: UnitOfWork, draft: NewEpisode) -> EpisodeRef:
        return self.service.create(uow, draft)

    def facts(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None:
        return self.queries.get(uow, episode_id)

    def record_route(
        self, uow: UnitOfWork, episode_id: str, module_id: str, area_answer_id: str, at: datetime
    ) -> None:
        ep = self.service.repo.require(uow, episode_id)
        if uow.actor.kind != "person" or uow.actor.id != ep.patient_id:
            raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "patient only")
        linked = self.service.providers.answers
        if linked is None or area_answer_id not in {a.answer_id for a in linked.for_episode(uow, episode_id)}:
            raise ProblemError(ErrorCode.INVALID_VALUE, "route answer not on episode")
        self.service.changed(
            uow,
            replace(ep, route_module_id=module_id),
            "route_recorded",
            cause=area_answer_id,
            occurred_at=at,
        )

    def view(self, uow: UnitOfWork, viewer: Viewer, episode_id: str) -> dict[str, object]:
        return self.queries.view(uow, viewer, episode_id)


class RecordsSourceFacade:
    def __init__(self, queries: EpisodeQueries) -> None:
        self.queries = queries

    def items(self, uow: UnitOfWork, person_id: str, cursor: str | None, limit: int) -> dict[str, object]:
        # Records owner authorizes the person before asking its in-process sources for rows.
        if not 1 <= limit <= 200:
            raise ProblemError(ErrorCode.INVALID_VALUE, "page bound")
        rows = uow.connection.rows(
            "SELECT episode_id,state,revision,created_at,origin_mode FROM episodes_episode WHERE patient_id=? AND episode_id>? ORDER BY episode_id LIMIT ?",
            (person_id, cursor or "", limit + 1),
            limit=limit + 1,
        )
        return {
            "items": [
                {
                    "kind": "episode",
                    "ref_id": row[0],
                    "state": row[1],
                    "revision": row[2],
                    "created_at": row[3],
                    "origin_mode": row[4],
                }
                for row in rows[:limit]
            ],
            "next_cursor": rows[limit - 1][0] if len(rows) > limit else None,
        }
