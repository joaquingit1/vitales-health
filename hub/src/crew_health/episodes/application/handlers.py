from __future__ import annotations

from datetime import datetime

from crew_health.episodes.application.codec import instant, text
from crew_health.episodes.application.dto import AnswerFact, LinkedFact
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.episodes.domain.types import LINK_KINDS, EpisodeState
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import UnitOfWork


class EpisodeHandlers:
    def __init__(self, service: EpisodeService) -> None:
        self.service = service

    def answer(self, uow: UnitOfWork, event: AnswerFact) -> None:
        s = self.service
        ep = s.repo.get(uow, event.episode_id)
        if ep is None:
            return
        if event.origin_mode != ep.origin_mode:
            s.audit(uow, ep, "link_refused")
            return
        kind = "answer_corrected" if event.supersedes is not None else "answer"
        if not s.repo.timeline(
            uow,
            ep,
            entry_id=s.ids.new("etl"),
            kind=kind,
            ref_id=event.answer_id,
            occurred_at=event.at,
            dedupe="answer:" + event.answer_id,
            source="answer",
        ):
            return
        if event.supersedes:
            self.correction(uow, event.episode_id, event.answer_id, event.supersedes)
        s.changed(
            uow,
            ep,
            kind,
            cause=event.answer_id,
            reopen=ep.state == EpisodeState.RESOLVED,
            occurred_at=event.at,
        )

    def linked(self, uow: UnitOfWork, event: LinkedFact) -> None:
        s = self.service
        ep = s.repo.get(uow, event.episode_id)
        if ep is None:
            return
        if event.origin_mode != ep.origin_mode:
            s.audit(uow, ep, "link_refused")
            return
        if event.kind not in LINK_KINDS and event.kind != "urgency":
            raise ProblemError(ErrorCode.INVALID_VALUE, "unknown link kind")
        dedupe = "link:" + event.event_id
        if not s.repo.timeline(
            uow,
            ep,
            entry_id=s.ids.new("etl"),
            kind="attempt"
            if event.attempt_id
            else "observation_corrected"
            if event.supersedes and event.kind == "observation"
            else event.kind,
            ref_id=event.attempt_id or event.ref_id,
            state=event.reason_code or event.state,
            occurred_at=event.at,
            dedupe=dedupe,
            source="link",
        ):
            return
        new = False
        if event.kind != "urgency":
            rows = uow.connection.rows(
                "SELECT episode_id,updated_at FROM episodes_link WHERE kind=? AND ref_id=?",
                (event.kind, event.ref_id),
                limit=1,
            )
            if rows and rows[0][0] != ep.episode_id:
                raise ProblemError(ErrorCode.INVALID_VALUE, "link belongs to another episode")
            if not rows:
                if len(s.repo.links(uow, ep.episode_id)) >= s.limits.max_links_per_episode:
                    raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "link bound")
                uow.connection.execute(
                    "INSERT INTO episodes_link VALUES (?,?,?,?,?,?,?)",
                    (
                        event.kind,
                        event.ref_id,
                        ep.episode_id,
                        event.state,
                        event.origin_mode,
                        instant(event.at),
                        instant(event.at),
                    ),
                )
                new = True
            elif datetime.fromisoformat(text(rows[0][1])) <= event.at:
                uow.connection.execute(
                    "UPDATE episodes_link SET ref_state=?,updated_at=? WHERE kind=? AND ref_id=?",
                    (event.state, instant(event.at), event.kind, event.ref_id),
                )
        if event.kind == "assessment" and event.is_model and new:
            s.open_task(uow, ep, "review", None, None, "assessment", event.ref_id)
        if event.supersedes:
            self.correction(uow, ep.episode_id, event.ref_id, event.supersedes)
        reopen = ep.state == EpisodeState.RESOLVED and (
            event.opens_urgency_cycle
            or (
                new
                and event.kind
                in ("measurement_request", "assessment", "plan_run", "help_session", "guided_check")
            )
        )
        s.changed(
            uow,
            ep,
            "urgency_raised" if event.opens_urgency_cycle else "new_activity" if reopen else event.kind,
            cause=event.ref_id,
            reopen=reopen,
            occurred_at=event.at,
        )

    def correction(self, uow: UnitOfWork, episode_id: str, record_id: str, superseded_id: str) -> None:
        # This intent is committed with the corrected record; optional E13 work runs after commit.
        ref = self.service.ids.derived("efx", episode_id, record_id)
        uow.connection.execute(
            "INSERT OR IGNORE INTO episodes_effect VALUES (?,?,?,?,?,?,?,?)",
            (ref, episode_id, record_id, superseded_id, "due", 0, instant(uow.now), None),
        )
