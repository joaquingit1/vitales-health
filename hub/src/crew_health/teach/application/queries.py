"""Read side: the GuidedCheck wire projection (guided-check.schema.json), the rendered teach steps (X1 interim)
and GuidedCheckQueries for E28 and E31. Every text is a catalog phrase or a ui-copy word (I30-12)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.config import TemplateRenderer
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.teach.application.content import TITLE_FIELD, PinnedContent, render_steps
from crew_health.teach.application.ports import (
    GuidedCheckFacts,
    GuidedCheckRepository,
    OriginMode,
    PersonDirectory,
)
from crew_health.teach.domain.agreement import display_decimals, rounded
from crew_health.teach.domain.capture import ClockReading
from crew_health.teach.domain.catalog import SkillDefinition
from crew_health.teach.domain.guided_check import GuidedCheck


def instant(value: datetime) -> str:
    """RFC 3339 UTC with Z and milliseconds."""
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def number(value: Decimal | int) -> int | float:
    """A wire number from an exact value already rounded for display; never NaN or infinity."""
    decimal = Decimal(value)
    if not decimal.is_finite():
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return int(decimal) if decimal == decimal.to_integral_value() else float(decimal)


def display_names(people: PersonDirectory, check: GuidedCheck) -> dict[str, str]:
    names: dict[str, str] = {}
    for key, person_id in (("helper_name", check.helper_id), ("subject_name", check.subject_id)):
        person = people.person(person_id)
        if person is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        names[key] = person.display_name
    return names


def _tolerance(skill: SkillDefinition) -> dict[str, object] | None:
    check = skill.count_check
    tol = check.tolerance if check is not None else None
    if tol is None or tol.raw_value is None:
        return None  # pending: the schema needs a number, so the projection is null (X19)
    return {
        "value": number(tol.raw_value),
        "unit": tol.unit_words,
        "basis": tol.basis,
        "to_be_set_by": tol.to_be_set_by,
    }


def _camera(check: GuidedCheck, decimals: int) -> dict[str, object] | None:
    camera = check.camera
    if camera is None:
        return None
    return {
        "observation_id": camera.observation_id,
        "value": None if camera.value is None else number(rounded(camera.value, decimals)),
        "unit": camera.unit,
        "no_reading_reason": camera.reason,
        "origin_mode": camera.origin_mode,
    }


def project(
    check: GuidedCheck,
    content: PinnedContent,
    names: Mapping[str, str],
    reading: ClockReading,
    *,
    allowed: list[str] | None = None,
) -> dict[str, object]:
    skill = content.skill(check.skill_id)
    count_step = skill.count_step(check.environment_id)
    reference = skill.count_check.reference_code if skill.count_check is not None else None
    base = content.decimals(reference) if reference is not None else 0
    tolerance = skill.count_check.tolerance if skill.count_check is not None else None
    camera_value = check.camera.value if check.camera is not None else None
    decimals = (
        base
        if check.count_exact is None or check.result is None
        else display_decimals(
            check.count_exact,
            camera_value,
            None if tolerance is None else tolerance.value,
            check.result,
            base,
        )
    )
    attempt = check.attempt
    window = attempt.window if attempt is not None and check.state != "cancelled" else None
    beats = check.visible_beats()
    count = check.count_exact
    return {
        "guided_check_id": check.guided_check_id,
        "revision": check.revision,
        "skill_id": check.skill_id,
        "check_id": check.check_id,
        "helper": {"person_id": check.helper_id, "display_name": names["helper_name"]},
        "subject": {"person_id": check.subject_id, "display_name": names["subject_name"]},
        "plan_run_id": check.plan_run_id,
        "drill_id": None,
        "episode_id": check.episode_id,
        "role_assignment_id": check.role_assignment_id,
        "state": check.state,
        "teach_step_no": check.teach_step_no,
        "steps_done": [{"step_no": n, "at": instant(at)} for n, at in check.steps_done],
        "count_seconds": None if count_step is None else count_step.count_seconds,
        "count_started_at": None if window is None else instant(window.started_utc),
        "count_ends_at": None if window is None else instant(window.ends_utc()),
        "beats": [instant(b.at_utc) for b in beats],
        "beat_count": len(beats),
        "count_per_minute": None if count is None else number(rounded(count, decimals)),
        "helper_answer": check.helper_answer,
        "answer_id": check.answer_id,
        "camera_reference": _camera(check, decimals),
        "result": check.result,
        "difference": None
        if check.difference_exact is None
        else number(rounded(check.difference_exact, decimals)),
        "tolerance": _tolerance(skill),
        "result_text": check.result_text,
        "what_it_shows": None
        if skill.what_it_shows is None
        else {
            "does": skill.what_it_shows.does,
            "does_not": skill.what_it_shows.does_not,
            "source_ids": list(skill.what_it_shows.source_ids),
        },
        "experimental": check.experimental,
        "observation_id": check.observation_id,
        "origin_mode": check.count_origin,
        "created_at": instant(check.created_at),
        "allowed_actions": allowed if allowed is not None else check.allowed_actions(skill, reading),
    }


def project_steps(
    check: GuidedCheck, content: PinnedContent, renderer: TemplateRenderer, names: Mapping[str, str]
) -> dict[str, object]:
    """The pinned, rendered teach steps a client shows one at a time (X1 proposal shape)."""
    skill = content.skill(check.skill_id)
    steps = render_steps(content, renderer, check.steps(skill), names)
    landmarks = content.catalog.landmarks
    return {
        "guided_check_id": check.guided_check_id,
        "revision": check.revision,
        "card_title_text": renderer.render(TITLE_FIELD, skill.card_title, names),
        "notice": content.catalog.notice,
        "review_state": skill.review_state,
        "config_pins": [{"name": k, "version": v} for k, v in sorted(check.pins.items())],
        "teach_steps": [
            {
                "step_no": s.step_no,
                "kind": s.kind,
                "actor": s.actor,
                "text": s.text,
                "text_first_time": s.text_first_time,
                "landmark_id": s.landmark_id,
                "landmark_label": None if s.landmark_id is None else landmarks[s.landmark_id].label,
                "body_region": s.body_region,
                "count_seconds": s.count_seconds,
                "choices": [{"answer": a, "label": label} for a, label in s.choices],
                "source_ids": list(s.source_ids),
            }
            for s in steps
        ],
    }


class GuidedCheckReader:
    """GuidedCheckQueries for E28 and E31, in the caller's unit of work or snapshot."""

    def __init__(self, repository: GuidedCheckRepository) -> None:
        self._repository = repository

    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheckFacts | None:
        check = self._repository.get(view, guided_check_id)
        if check is None:
            return None
        written = check.observation_id if check.measured_record_id == check.subject_id else None
        origin: OriginMode = "SIM" if check.count_origin == "SIM" else "LIVE"
        return GuidedCheckFacts(
            guided_check_id=check.guided_check_id,
            skill_id=check.skill_id,
            plan_run_id=check.plan_run_id,
            subject_person_id=check.subject_id,
            helper_person_id=check.helper_id,
            measured_record_id=check.measured_record_id,
            state=check.state,
            result=check.result,
            origin_mode=origin,
            observation_ids=() if written is None else (written,),
        )
