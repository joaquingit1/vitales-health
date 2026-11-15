from crew_health.episodes.application.codec import instant
from crew_health.episodes.application.ports import SelectedEpisodeField, SelectedEpisodeSnapshot
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import ReadSnapshot, SessionContext


class SelectedEpisodeReader:
    def __init__(self, service: EpisodeService) -> None:
        self.service = service

    def read(
        self, view: ReadSnapshot, session: SessionContext, *, episode_id: str, fields: frozenset[str]
    ) -> SelectedEpisodeSnapshot:
        if not fields or not fields <= {"concern_text", "onset", "answer_refs"}:
            raise ProblemError(ErrorCode.INVALID_VALUE, "unsupported selected episode fields")
        ep = self.service.repo.require(view, episode_id)
        access = self.service.authorize(view, session, ep, fields=fields)
        rules = self.service.rules.rules(ep.config_release_id)
        selected: list[SelectedEpisodeField] = []
        for key in sorted(fields):
            observed = ep.created_at
            refs: tuple[str, ...] = (episode_id,)
            value: str | None = None
            missing = None
            if key == "concern_text":
                value = ep.concern_text
            elif key == "onset":
                if ep.onset.at is None:
                    missing = "not_recorded"
                else:
                    value = f"{instant(ep.onset.at)} ({ep.onset.precision})"
                    observed = ep.onset.at
            else:
                provider = self.service.providers.answers
                if provider is None:
                    raise ProblemError(
                        ErrorCode.INTERNAL_ERROR, "selected answers unavailable", retryable=True
                    )
                answers = tuple(provider.for_episode(view, episode_id))
                if len(answers) > self.service.limits.max_answers_in_view:
                    raise ProblemError(ErrorCode.INTERNAL_ERROR, "selected answer bound", retryable=True)
                for answer in answers:
                    if answer.patient_id != ep.patient_id or answer.episode_id != episode_id:
                        raise ProblemError(ErrorCode.INTERNAL_ERROR, "selected answer scope mismatch")
                    if access != "full":
                        authorization = self.service.providers.authorization
                        if authorization is None:
                            raise ProblemError(ErrorCode.NOT_FOUND, "selected answer not shared")
                        authorization.require_read(
                            view,
                            session,
                            object_ref=answer.answer_id,
                            patient_id=ep.patient_id,
                            fields=frozenset({"answer_id", "answered_at"}),
                        )
                refs = tuple(a.answer_id for a in answers)
                value = ", ".join(refs) if refs else None
                missing = None if refs else "not_recorded"
                observed = max((a.answered_at for a in answers), default=ep.created_at)
            selected.append(
                SelectedEpisodeField(key, rules.selected_field_labels[key], value, refs, observed, missing)
            )
        return SelectedEpisodeSnapshot(
            ep.episode_id,
            ep.patient_id,
            ep.revision,
            ep.origin_mode,
            ep.config_release_id,
            rules.version,
            tuple(selected),
        )
