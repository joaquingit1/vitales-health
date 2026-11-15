from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from crew_health.episodes.application import ports
from crew_health.episodes.application.codec import array, decode, instant, integer, obj, text
from crew_health.episodes.application.dto import (
    CrewMember,
    EpisodeChanged,
    EpisodeCreated,
    EpisodeRef,
    NewEpisode,
)
from crew_health.episodes.application.repository import EpisodeRepository
from crew_health.episodes.application.rules import EpisodeRulesSource
from crew_health.episodes.domain.due_policy import due_time
from crew_health.episodes.domain.errors import refused
from crew_health.episodes.domain.followup import task_action
from crew_health.episodes.domain.onset import Onset
from crew_health.episodes.domain.resolution import check_resolution
from crew_health.episodes.domain.state_machine import next_state
from crew_health.episodes.domain.types import (
    MR_PENDING,
    PLAN_RUN_OPEN,
    REOPEN_REASONS,
    Episode,
    EpisodeState,
    EvidenceObservation,
    ResolutionCommand,
    ResolutionFacts,
    ResolutionReason,
)
from crew_health.episodes.domain.visibility import visibility
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    IdempotencyStore,
    StoredResult,
    canonical_json,
)
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
)
from crew_health.roles.application.ports import AuthorityDecision, ClinicalAuthorityQuery

type View = ReadSnapshot | UnitOfWork


@dataclass(slots=True)
class Providers:
    authorization: ports.RecordAuthorization | None = None
    clinical_authority: ClinicalAuthorityQuery | None = None
    delivered: ports.DeliveredEpisodeReader | None = None
    answers: ports.AnswerStore | None = None
    gaps: ports.GapFacts | None = None
    observations: ports.ObservationFacts | None = None
    urgency: ports.UrgencyQueries | None = None
    roster: ports.RosterPort | None = None
    consent: ports.ConsentQueries | None = None
    sensitivity: ports.QuestionSensitivity | None = None
    origin: ports.OriginNamespace | None = None
    assessments: ports.AssessmentFacts | None = None
    assessment_commands: ports.AssessmentCommands | None = None
    mission_clock: ports.MissionClock | None = None
    day_labels: ports.DayLabelPort | None = None


@dataclass(frozen=True, slots=True)
class Limits:
    max_open_tasks: int
    max_task_text_chars: int
    max_answers_in_view: int
    max_links_per_episode: int
    effect_batch_size: int
    effect_call_timeout_ms: int
    effect_retry_backoff_seconds: int
    effect_max_attempts: int
    overdue_tick_seconds: int
    overdue_batch_size: int


class EpisodeService:
    def __init__(
        self,
        repo: EpisodeRepository,
        ids: IdFactory,
        rules: EpisodeRulesSource,
        providers: Providers,
        limits: Limits,
        ledger: IdempotencyStore,
    ) -> None:
        self.repo, self.ids, self.rules, self.providers, self.limits, self.ledger = (
            repo,
            ids,
            rules,
            providers,
            limits,
            ledger,
        )

    def replication(self, uow: UnitOfWork, ep: Episode, operation: Literal["create", "version"]) -> None:
        uow.replicate(
            ReplicationChange(
                None,
                "episode",
                ep.episode_id,
                operation,
                canonical_json(
                    {"episode_id": ep.episode_id, "revision": ep.revision, "state": ep.state.value}
                ),
                (),
                ep.config_release_id,
                Audience(
                    "episodes.owner", ep.episode_id, AudienceProjectionKey("episodes", "episode", 1), "1"
                ),
            )
        )

    def audit(self, uow: UnitOfWork, ep: Episode, kind: str) -> None:
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                "episode." + kind,
                1,
                uow.actor,
                {"episode": ep.episode_id},
                uow.correlation_id,
                None,
                {},
            )
        )

    def changed(
        self,
        uow: UnitOfWork,
        ep: Episode,
        kind: str,
        *,
        cause: str | None = None,
        reopen: bool = False,
        resolve: bool = False,
        occurred_at: datetime | None = None,
        preserve_state: bool = False,
    ) -> Episode:
        answered: frozenset[str] = frozenset()
        if ep.state == EpisodeState.DRAFT and not preserve_state:
            if self.providers.answers is None:
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "answer provider unavailable", retryable=True)
            answered = self.providers.answers.question_ids_answered(uow, ep.episode_id)
        state = (
            ep.state
            if preserve_state
            else next_state(
                ep,
                self.repo.task_facts(uow, ep.episode_id),
                self.repo.link_facts(uow, ep.episode_id),
                answered=answered,
                reopen=reopen,
                resolve=resolve,
            )
        )
        if ep.state == EpisodeState.RESOLVED and reopen:
            reason = {
                "answer": "answer_after_resolution",
                "answer_corrected": "answer_after_resolution",
                "reviewed": "review_unresolved",
                "urgency_raised": "urgency_raised",
                "new_activity": "new_activity",
                "evidence_changed": "evidence_changed",
            }.get(kind, kind)
            if kind == "reopened":
                reason = cause or "recorded_in_error"
            self.reopen_record(uow, ep, reason, cause)
        updated = self.repo.change(uow, ep, state, kind, cause=cause)
        self.repo.timeline(
            uow,
            updated,
            entry_id=self.ids.new("etl"),
            kind=kind,
            ref_id=cause or ep.episode_id,
            state=state.value,
            occurred_at=occurred_at,
        )
        self.replication(uow, updated, "version")
        self.audit(uow, updated, kind)
        uow.publish(
            EpisodeChanged(ep.episode_id, updated.revision, ep.state.value, state.value, kind, uow.now)
        )
        return updated

    def create(self, uow: UnitOfWork, draft: NewEpisode) -> EpisodeRef:
        if (
            uow.actor.kind != "person"
            or uow.actor.id != draft.patient_id
            or draft.created_by.person_id != draft.patient_id
        ):
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        if self.providers.origin is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "namespace unavailable", retryable=True)
        if self.providers.origin.origin(uow, draft.patient_id, draft.mission_id) != draft.origin_mode:
            raise refused(ErrorCode.ORIGIN_MISMATCH)
        onset = Onset(draft.onset.at, cast(Literal["exact", "hour", "day", "unknown"], draft.onset.precision))
        onset.validate_at(uow.now)
        if (
            not draft.concern_text.strip()
            or len(draft.concern_text) > self.limits.max_task_text_chars
            or len(draft.red_flag_ids) > self.limits.max_answers_in_view
            or len(set(draft.red_flag_ids)) != len(draft.red_flag_ids)
        ):
            raise refused(ErrorCode.INVALID_VALUE)
        ep = Episode(
            self.ids.new("epi"),
            draft.patient_id,
            draft.mission_id,
            draft.origin_mode,
            draft.concern_text,
            onset,
            EpisodeState.DRAFT,
            1,
            uow.now,
            uow.actor,
            draft.questionnaire_version,
            draft.red_flag_ids,
            None,
            draft.config_release_id,
        )
        self.repo.create(uow, ep)
        self.repo.timeline(uow, ep, entry_id=self.ids.new("etl"), kind="created", ref_id=ep.episode_id)
        self.replication(uow, ep, "create")
        self.audit(uow, ep, "created")
        uow.publish(
            EpisodeCreated(
                ep.episode_id,
                ep.patient_id,
                ep.mission_id,
                cast(Literal["LIVE", "REPLAY", "SIM"], ep.origin_mode),
                uow.now,
            )
        )
        return EpisodeRef(ep.episode_id, 1)

    def authorize(
        self,
        view: View,
        session: SessionContext,
        ep: Episode,
        *,
        fields: frozenset[str] = frozenset({"episode"}),
        ground_advice: bool = False,
    ) -> str:
        if session.actor.kind == "assistant":
            raise refused(ErrorCode.UNAUTHORIZED_SCOPE)
        if (
            session.kind == "crew"
            and session.actor.kind == "person"
            and session.person_id == session.actor.id
        ):
            if session.person_id == ep.patient_id:
                return visibility(session, ep.patient_id)
            if self.providers.authorization is None:
                raise refused(ErrorCode.NOT_FOUND)
            self.providers.authorization.require_read(
                view, session, object_ref=ep.episode_id, patient_id=ep.patient_id, fields=fields
            )
            return visibility(session, ep.patient_id, object_grant=True)
        if (
            ground_advice
            and session.kind == "ground"
            and session.actor.kind == "ground"
            and "ground" in session.roles
        ):
            if (
                self.providers.consent is not None
                and self.providers.consent.allows(
                    view, ep.patient_id, "ground", ("episode", "clinical_review")
                )
                and self.providers.authorization is not None
            ):
                self.providers.authorization.require_read(
                    view,
                    session,
                    object_ref=ep.episode_id,
                    patient_id=ep.patient_id,
                    fields=frozenset({"record_review"}),
                )
                return "advice"
            raise refused(ErrorCode.CONSENT_REQUIRED)
        raise refused(ErrorCode.UNAUTHORIZED_SCOPE)

    def authority(
        self,
        view: View,
        session: SessionContext,
        ep: Episode,
        *,
        action: Literal["record_review", "clinician_assessed"] = "record_review",
    ) -> AuthorityDecision:
        if (
            session.kind != "crew"
            or session.actor.kind != "person"
            or session.actor.id != session.person_id
            or session.person_id == ep.patient_id
        ):
            return AuthorityDecision("none", None, "not an independent human clinician")
        if self.providers.clinical_authority is None:
            return AuthorityDecision("none", None, "clinical authority unavailable")
        return self.providers.clinical_authority.authority(
            view,
            person_id=session.actor.id,
            subject_person_id=ep.patient_id,
            action=action,
            dose=None,
            at=view.now,
        )

    def human(
        self, view: View, session: SessionContext, ep: Episode, *, require_officer: bool = False
    ) -> bool:
        if session.kind != "crew" or session.actor.kind != "person" or session.actor.id != session.person_id:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        is_officer = self.authority(view, session, ep).kind != "none"
        if require_officer and not is_officer:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        if not is_officer and session.person_id != ep.patient_id:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        return is_officer

    def replay(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        ep: Episode,
        identity: CommandIdentity,
        *,
        ground_advice: bool = False,
    ) -> str | None:
        if identity.actor_ref != session.actor.id:
            raise refused(ErrorCode.UNAUTHORIZED_SCOPE)
        stored = self.ledger.lookup(uow, identity)
        self.authorize(uow, session, ep, ground_advice=ground_advice)
        return stored.resource_ref if stored is not None else None

    def receipt(self, uow: UnitOfWork, identity: CommandIdentity, resource: str, status: int) -> None:
        receipt = AcceptedReceipt(
            identity.key, identity.operation, identity.target, self.ids.new("receipt"), uow.now
        )
        self.ledger.record(uow, identity, StoredResult(receipt, status, resource, "accepted", None))

    def version(self, ep: Episode, expected: int | None) -> None:
        if expected is None:
            raise refused(ErrorCode.VERSION_REQUIRED)
        if expected != ep.revision:
            raise ProblemError(
                ErrorCode.STALE_VERSION,
                "episode revision changed",
                current_state={"episode_id": ep.episode_id, "revision": ep.revision, "state": ep.state.value},
            )

    def act(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        episode_id: str,
        action: Mapping[str, object],
        expected: int | None,
        identity: CommandIdentity,
    ) -> Episode:
        if uow.actor != session.actor:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        ep = self.repo.require(uow, episode_id)
        if self.replay(uow, session, ep, identity) is not None:
            return ep
        is_officer = self.human(uow, session, ep)
        self.version(ep, expected)
        kind = text(action.get("action"))
        if ep.state == EpisodeState.RESOLVED and kind in ("request_review", "add_followup"):
            raise refused(ErrorCode.INVALID_TRANSITION)
        if kind == "resolve":
            ep = self.resolve(uow, session, ep, action, is_officer)
        elif kind == "reopen":
            if ep.state != EpisodeState.RESOLVED or action.get("reason") not in REOPEN_REASONS:
                raise refused(ErrorCode.INVALID_TRANSITION)
            ep = self.changed(uow, ep, "reopened", cause=text(action.get("reason")), reopen=True)
        elif kind == "acknowledge":
            self.human(uow, session, ep, require_officer=True)
            self.record(
                uow, "ack", self.ids.new("ack"), ep, {"by": actor_wire(uow.actor), "at": instant(uow.now)}
            )
            ep = self.changed(uow, ep, "acknowledged")
        elif kind == "request_review":
            self.open_task(uow, ep, "review", None, None, "request", identity.key)
            ep = self.changed(uow, ep, "review_requested")
        elif kind == "add_followup":
            owner = text(action.get("owner_person_id", ep.patient_id))
            if not is_officer and owner != ep.patient_id:
                raise refused(ErrorCode.ACTION_NOT_ALLOWED)
            self.open_task(
                uow,
                ep,
                "custom",
                required_text(action.get("action_text"), self.limits.max_task_text_chars),
                owner,
                "person",
                identity.key,
                chosen=parse_time(action.get("due_at")),
            )
            ep = self.changed(uow, ep, "followup_opened")
        elif kind in ("complete_followup", "cancel_followup", "reassign_followup"):
            self.modify_task(uow, session, ep, action, is_officer)
            ep = self.changed(
                uow,
                ep,
                {
                    "complete_followup": "followup_completed",
                    "cancel_followup": "followup_cancelled",
                    "reassign_followup": "followup_reassigned",
                }[kind],
            )
        elif kind == "correct_onset":
            if session.person_id != ep.patient_id:
                raise refused(ErrorCode.ACTION_NOT_ALLOWED)
            raw = obj(action.get("onset"))
            onset = Onset(
                parse_time(raw.get("at")),
                cast(Literal["exact", "hour", "day", "unknown"], text(raw.get("precision"))),
            )
            onset.validate_at(uow.now)
            ep = replace(ep, onset=onset)
            self.repo.onset(uow, ep)
            ep = self.changed(uow, ep, "onset_corrected")
        else:
            raise refused(ErrorCode.INVALID_VALUE, "action")
        self.receipt(uow, identity, episode_id, 200)
        return ep

    def record(self, uow: UnitOfWork, kind: str, ref: str, ep: Episode, value: dict[str, object]) -> None:
        allowed = {"ack": "ack_id", "resolution": "resolution_id", "reopen": "reopen_id"}
        if kind not in allowed:
            raise refused(ErrorCode.INTERNAL_ERROR)
        data = {allowed[kind]: ref, **value}
        if kind == "resolution":
            uow.connection.execute(
                "INSERT INTO episodes_resolution VALUES (?,?,?,?)",
                (ref, ep.episode_id, ep.revision, canonical_json(data)),
            )
        elif kind == "reopen":
            uow.connection.execute(
                "INSERT INTO episodes_reopen VALUES (?,?,?,?)",
                (ref, ep.episode_id, ep.last_resolution_id, canonical_json(data)),
            )
        else:
            uow.connection.execute(
                "INSERT INTO episodes_ack VALUES (?,?,?)", (ref, ep.episode_id, canonical_json(data))
            )

    def reopen_record(self, uow: UnitOfWork, ep: Episode, reason: str, cause: str | None) -> None:
        self.record(
            uow,
            "reopen",
            self.ids.new("rop"),
            ep,
            {"reason": reason, "by": actor_wire(uow.actor), "at": instant(uow.now), "cause_ref": cause},
        )

    def resolve(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        ep: Episode,
        action: Mapping[str, object],
        is_officer: bool,
    ) -> Episode:
        if self.providers.urgency is None or self.providers.answers is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "resolution facts unavailable", retryable=True)
        try:
            reason = ResolutionReason(text(action.get("reason")))
        except ValueError:
            raise refused(ErrorCode.INVALID_VALUE, "reason") from None
        refs = tuple(text(v) for v in array(action.get("observation_ids", [])))
        observations = []
        if refs and self.providers.observations is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "observations unavailable", retryable=True)
        for ref in refs:
            item = self.providers.observations.get(uow, ref) if self.providers.observations else None
            if item is not None:
                observations.append(
                    EvidenceObservation(
                        item.observation_id,
                        item.patient_id,
                        item.episode_id,
                        item.quality_status,
                        item.observed_at,
                    )
                )
        review_id = action.get("review_id")
        review = None
        if isinstance(review_id, str):
            rows = uow.connection.rows(
                "SELECT data FROM episodes_review WHERE review_id=? AND episode_id=?",
                (review_id, ep.episode_id),
                limit=1,
            )
            review = decode(rows[0][0]) if rows else None
        links = self.repo.link_facts(uow, ep.episode_id)
        correction = uow.connection.rows(
            "SELECT max(recorded_at) FROM episodes_timeline WHERE episode_id=? AND kind IN (?,?)",
            (ep.episode_id, "answer_corrected", "observation_corrected"),
            limit=1,
        )[0][0]
        facts = ResolutionFacts(
            self.providers.urgency.blocks_resolution(uow, ep.episode_id),
            bool(self.repo.task_facts(uow, ep.episode_id)),
            any(link.kind == "plan_run" and link.state in PLAN_RUN_OPEN for link in links),
            any(link.kind == "measurement_request" and link.state in MR_PENDING for link in links),
            any(a.presence == "present" for a in self.providers.answers.heads(uow, ep.episode_id).values()),
            tuple(observations),
            text(review["review_id"]) if review else None,
            text(obj(review["reviewer"])["id"]) if review else None,
            text(review["role"]) if review else None,
            parse_time(review["signed_at"]) if review else None,
            parse_time(correction),
        )
        cmd = ResolutionCommand(
            reason,
            text(action["rationale"]) if action.get("rationale") is not None else None,
            refs,
            text(review_id) if review_id is not None else None,
            session.actor.id,
            self.authority(uow, session, ep, action="clinician_assessed").kind if is_officer else "patient",
        )
        check_resolution(ep, cmd, facts)
        ref = self.ids.new("res")
        self.record(
            uow,
            "resolution",
            ref,
            ep,
            {
                "reason": reason.value,
                "rationale": cmd.rationale,
                "by": actor_wire(uow.actor),
                "authority": cmd.authority,
                "at": instant(uow.now),
                "evidence": {"observation_ids": list(refs), "review_id": cmd.review_id},
            },
        )
        return self.changed(uow, replace(ep, last_resolution_id=ref), "resolved", resolve=True)

    def choose_owner(self, at: datetime, patient_id: str, owner: str | None, kind: str) -> CrewMember:
        if self.providers.roster is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "roster unavailable", retryable=True)
        roster = tuple(self.providers.roster.crew(at))
        if len(roster) > self.limits.max_open_tasks:
            raise refused(ErrorCode.INTERNAL_ERROR, "roster", "bound")
        candidates = [
            p
            for p in roster
            if (owner is None or p.person_id == owner)
            and (kind != "review" or (p.medical_officer_rank is not None and p.person_id != patient_id))
        ]
        if not candidates:
            raise refused(ErrorCode.INVALID_VALUE, "owner_person_id", "reviewer_required")
        return min(
            candidates,
            key=lambda p: (
                p.medical_officer_rank if p.medical_officer_rank is not None else 2147483647,
                p.person_id,
            ),
        )

    def open_task(
        self,
        uow: UnitOfWork,
        ep: Episode,
        kind: str,
        action_text: str | None,
        owner_id: str | None,
        source_kind: str,
        source_ref: str,
        *,
        chosen: datetime | None = None,
    ) -> str:
        if kind == "review":
            existing = uow.connection.rows(
                "SELECT task_id FROM episodes_task WHERE episode_id=? AND kind=? AND state=?",
                (ep.episode_id, "review", "open"),
                limit=1,
            )
            if existing:
                ref = text(existing[0][0])
                uow.connection.execute(
                    "INSERT OR IGNORE INTO episodes_task_source VALUES (?,?,?,?)",
                    (ref, source_kind, source_ref, instant(uow.now)),
                )
                return ref
        if len(self.repo.task_facts(uow, ep.episode_id)) >= self.limits.max_open_tasks:
            raise refused(ErrorCode.INVALID_VALUE, "followup_tasks", "limit")
        owner = self.choose_owner(uow.now, ep.patient_id, owner_id, kind)
        release_id = self.rules.active_release_id
        rules = self.rules.rules(release_id)
        policy = rules.task_policies[kind]
        day_end = (
            self.providers.mission_clock.day_end(uow.now)
            if policy.kind == "end_of_mission_day" and self.providers.mission_clock is not None
            else None
        )
        due, status = due_time(policy, uow.now, chosen=chosen, day_end=day_end)
        key = {
            "assessment": "review_assessment",
            "correction": "review_correction",
            "request": "review_requested",
        }.get(source_kind, "review_requested")
        label = self.providers.day_labels.inline_label_at(uow.now) if self.providers.day_labels else ""
        rendered = self.rules.render(release_id, key, uow.now, label) if kind == "review" else action_text
        if not rendered:
            raise refused(ErrorCode.INVALID_VALUE, "action_text")
        ref = self.ids.new("tsk")
        data = {
            "task_id": ref,
            "kind": kind,
            "revision": 1,
            "action": rendered,
            "owner": {"kind": "person", "id": owner.person_id, "display_name": owner.display_name},
            "state": "open",
            "due_at": instant(due) if due else None,
            "due_status": status,
            "due_policy_id": policy.policy_id,
            "config_release_id": release_id,
            "created_at": instant(uow.now),
            "created_by": actor_wire(uow.actor),
            "closed": None,
            "overdue": False,
        }
        uow.connection.execute(
            "INSERT INTO episodes_task VALUES (?,?,?,?,?,?,?,?)",
            (
                ref,
                ep.episode_id,
                kind,
                owner.person_id,
                "open",
                1,
                instant(due) if due else None,
                canonical_json(data),
            ),
        )
        uow.connection.execute(
            "INSERT INTO episodes_task_event VALUES (?,?,?,?,?,?)",
            (ref, 1, "opened", instant(uow.now), uow.actor.id, canonical_json(data)),
        )
        uow.connection.execute(
            "INSERT INTO episodes_task_source VALUES (?,?,?,?)",
            (ref, source_kind, source_ref, instant(uow.now)),
        )
        return ref

    def modify_task(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        ep: Episode,
        action: Mapping[str, object],
        is_officer: bool,
        *,
        review_id: str | None = None,
    ) -> None:
        evidence_ref = action.get("evidence_ref")
        if evidence_ref is not None and evidence_ref not in {
            row[1] for row in self.repo.links(uow, ep.episode_id)
        }:
            raise refused(ErrorCode.INVALID_VALUE, "evidence_ref", "not_linked")
        task_id = text(action.get("task_id"))
        rows = uow.connection.rows(
            "SELECT kind,owner_id,state,revision,data FROM episodes_task WHERE task_id=? AND episode_id=?",
            (task_id, ep.episode_id),
            limit=1,
        )
        if not rows:
            raise refused(ErrorCode.NOT_FOUND)
        row = rows[0]
        data = decode(row[4])
        kind = text(row[0])
        old_revision = integer(row[3])
        act = text(action.get("action"))
        if row[2] != "open":
            raise refused(ErrorCode.INVALID_TRANSITION)
        if action.get("task_revision") != old_revision:
            raise refused(ErrorCode.STALE_VERSION)
        if review_id is None:
            task_action(
                act,
                kind=kind,
                owner=text(row[1]),
                creator=text(obj(data["created_by"])["id"]),
                actor=session.actor.id,
                patient=ep.patient_id,
                is_officer=is_officer,
                reason=text(action["cancellation_reason"])
                if action.get("cancellation_reason") is not None
                else None,
            )
        revision = old_revision + 1
        state = "open"
        owner = text(row[1])
        if act == "reassign_followup":
            chosen = self.choose_owner(uow.now, ep.patient_id, text(action.get("owner_person_id")), kind)
            owner = chosen.person_id
            data["owner"] = {"kind": "person", "id": owner, "display_name": chosen.display_name}
        else:
            state = "done" if act == "complete_followup" else "cancelled"
            data["closed"] = {
                "at": instant(uow.now),
                "by": actor_wire(uow.actor),
                "note": action.get("note"),
                "review_id": review_id,
                "cancellation_reason": action.get("cancellation_reason"),
            }
        data.update(state=state, revision=revision)
        uow.connection.execute(
            "INSERT INTO episodes_task_event VALUES (?,?,?,?,?,?)",
            (task_id, revision, act, instant(uow.now), uow.actor.id, canonical_json(data)),
        )
        uow.connection.execute(
            "UPDATE episodes_task SET owner_id=?,state=?,revision=?,data=? WHERE task_id=?",
            (owner, state, revision, canonical_json(data), task_id),
        )

    def review(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        episode_id: str,
        body: Mapping[str, object],
        expected: int | None,
        identity: CommandIdentity,
    ) -> dict[str, object]:
        if uow.actor != session.actor:
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        ep = self.repo.require(uow, episode_id)
        old = self.replay(uow, session, ep, identity, ground_advice=session.kind == "ground")
        if old is not None:
            row = uow.connection.rows("SELECT data FROM episodes_review WHERE review_id=?", (old,), limit=1)
            return decode(row[0][0])
        ground = session.kind == "ground"
        if session.actor.kind == "assistant" or (ground and session.actor.kind != "ground"):
            raise refused(ErrorCode.ACTION_NOT_ALLOWED)
        if not ground:
            self.human(uow, session, ep, require_officer=True)
        self.version(ep, expected)
        assessment = body.get("assessment_id")
        if assessment is not None:
            if self.providers.assessments is None:
                raise ProblemError(
                    ErrorCode.INTERNAL_ERROR, "assessment provider unavailable", retryable=True
                )
            if assessment not in self.providers.assessments.ids_for_episode(uow, episode_id):
                raise refused(ErrorCode.INVALID_VALUE, "assessment_id")
        unresolved = [
            required_text(v, self.limits.max_task_text_chars) for v in array(body.get("unresolved", []))
        ]
        decisions = array(body.get("decisions", []))
        if len(unresolved) > self.limits.max_open_tasks or len(decisions) > self.limits.max_open_tasks:
            raise refused(ErrorCode.PAYLOAD_TOO_LARGE)
        for decision in decisions:
            item = obj(decision)
            required_text(item.get("item"), self.limits.max_task_text_chars)
            required_text(item.get("rationale"), self.limits.max_task_text_chars)
            if item.get("decision") not in ("agree", "disagree", "needs_more", "not_assessed"):
                raise refused(ErrorCode.INVALID_VALUE, "decision")
        supersedes = body.get("supersedes_review_id")
        if supersedes is not None and not uow.connection.rows(
            "SELECT 1 FROM episodes_review WHERE review_id=? AND episode_id=? AND reviewer_id=?",
            (text(supersedes), episode_id, session.actor.id),
            limit=1,
        ):
            raise refused(ErrorCode.INVALID_VALUE, "supersedes_review_id")
        ref = self.ids.new("rev")
        decision = None if ground else self.authority(uow, session, ep)
        role = "ground" if decision is None else decision.kind
        result = {
            "review_id": ref,
            "episode_id": episode_id,
            "reviewer": actor_wire(session.actor),
            "role": role,
            "assessment_id": assessment,
            "decisions": decisions,
            "unresolved": unresolved,
            "signed_at": instant(uow.now),
            "authority": role,
            "role_assignment_id": decision.role_assignment_id if decision is not None else None,
            "episode_revision_seen": ep.revision,
            "supersedes_review_id": supersedes,
        }
        uow.connection.execute(
            "INSERT INTO episodes_review VALUES (?,?,?,?,?,?,?)",
            (ref, episode_id, session.actor.id, role, instant(uow.now), ep.revision, canonical_json(result)),
        )
        if not ground:
            tasks = uow.connection.rows(
                "SELECT task_id,revision FROM episodes_task WHERE episode_id=? AND kind=? AND state=?",
                (episode_id, "review", "open"),
                limit=1,
            )
            for task in tasks:
                self.modify_task(
                    uow,
                    session,
                    ep,
                    {"action": "complete_followup", "task_id": task[0], "task_revision": task[1]},
                    True,
                    review_id=ref,
                )
            for unresolved_text in unresolved:
                self.open_task(uow, ep, "review_item", unresolved_text, session.person_id, "review", ref)
            if isinstance(assessment, str):
                if self.providers.assessment_commands is None:
                    raise ProblemError(
                        ErrorCode.INTERNAL_ERROR, "assessment commands unavailable", retryable=True
                    )
                self.providers.assessment_commands.mark_reviewed(uow, assessment, ref)
        self.changed(
            uow,
            ep,
            "reviewed",
            cause=ref,
            preserve_state=ground,
            reopen=not ground and bool(unresolved) and ep.state == EpisodeState.RESOLVED,
        )
        self.receipt(uow, identity, ref, 201)
        return result


def actor_wire(actor: Actor) -> dict[str, object]:
    return {"kind": actor.kind, "id": actor.id, "display_name": actor.id}


def required_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise refused(ErrorCode.INVALID_VALUE)
    return value


def parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(text(value))
    except ValueError:
        raise refused(ErrorCode.INVALID_VALUE, "instant") from None
