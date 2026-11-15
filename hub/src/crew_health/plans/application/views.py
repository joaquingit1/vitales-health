"""Computed plan content, using one release and one snapshot for every response."""

from __future__ import annotations

import json
from dataclasses import dataclass

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.config import ConfigName
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.uow import SessionContext
from crew_health.plans.application.ground_log import GroundLine, text
from crew_health.plans.application.release import (
    UiCopy,
    array_value,
    object_value,
    read_document,
)
from crew_health.plans.application.view_steps import StepViews
from crew_health.plans.application.wire import instant
from crew_health.plans.application.world import PlansWorld, View, fail, internal
from crew_health.plans.domain.availability import EnvContext
from crew_health.plans.domain.model import Plan, PlansDocument, all_steps
from crew_health.plans.domain.view import plan_aboard, return_applies, view_stages, waits_for_medical_officer


@dataclass(frozen=True)
class PlanProjection:
    plan: Plan
    wire: dict[str, object]
    runnable: dict[str, bool]


class PlanViews:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def condition_plan(
        self, session: SessionContext, condition_id: str, environment_id: str | None
    ) -> dict[str, object]:
        self._authorize(session)
        with self.world.snapshots.snapshot() as view:
            kit = read_document(view.release, ConfigName.KIT_MAP)
            row = (
                next(
                    (
                        r
                        for v in array_value(kit.get("conditions", []))
                        if (r := object_value(v)).get("condition_id") == condition_id
                    ),
                    None,
                )
                if kit
                else None
            )
            if row is None:
                raise fail(ErrorCode.NOT_FOUND)
            # E28 5.3: E27 writes this same row-to-plan mapping when plan_id is not yet populated.
            plan_id = row.get("plan_id") or "plan_" + condition_id.lower()
            doc = self.world.catalog.document(view.release)
            plan = None if doc is None else doc.plan(str(plan_id))
            env = self._environment(view, environment_id)
            if doc is None or plan is None or env not in plan.environments:
                raise fail(ErrorCode.CONTEXT_INAPPLICABLE)
            return self._render(view, session, doc, plan, env)

    def plan(self, session: SessionContext, plan_id: str, environment_id: str | None) -> dict[str, object]:
        self._authorize(session)
        with self.world.snapshots.snapshot() as view:
            doc = self.world.catalog.document(view.release)
            plan = None if doc is None else doc.plan(plan_id)
            if doc is None or plan is None:
                raise fail(ErrorCode.NOT_FOUND)
            return self._render(view, session, doc, plan, self._environment(view, environment_id))

    def scoped(
        self, view: View, session: SessionContext, plan_id: str, subject_person_id: str
    ) -> PlanProjection | None:
        self._authorize(session)
        document = self.world.catalog.document(view.release)
        plan = None if document is None else document.plan(plan_id)
        if document is None or plan is None:
            return None
        return self._projection(
            view, session, document, plan, self._environment(view, None), subject_person_id
        )

    @staticmethod
    def _authorize(session: SessionContext) -> None:
        permitted = {
            Role.CREW,
            Role.CREW_MEDICAL_OFFICER,
            Role.GROUND,
            Role.OPERATOR,
            Role.STATION,
            Role.ADAPTER,
            Role.EDITOR,
        }
        if session.actor.kind == "assistant" or not session.roles & permitted:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)

    def _environment(self, view: View, explicit: str | None) -> str:
        if explicit is None:
            mission = self.world.current_mission(view)
            if mission is None:
                raise fail(ErrorCode.CONTEXT_INAPPLICABLE)
            explicit = mission.active_environment_id
        profiles = read_document(view.release, ConfigName.ENVIRONMENT_PROFILES)
        known = (
            ()
            if profiles is None
            else tuple(
                object_value(v).get("environment_id") for v in array_value(profiles.get("profiles", []))
            )
        )
        if explicit not in known:
            raise fail(ErrorCode.INVALID_VALUE, "/environment_id")
        return explicit

    def _render(
        self, view: View, session: SessionContext, doc: PlansDocument, plan: Plan, environment: str
    ) -> dict[str, object]:
        subject = session.person_id if session.kind == "crew" else None
        return self._projection(view, session, doc, plan, environment, subject).wire

    def _projection(
        self,
        view: View,
        session: SessionContext,
        doc: PlansDocument,
        plan: Plan,
        environment: str,
        subject: str | None,
    ) -> PlanProjection:
        copy = UiCopy.from_release(view.release)
        if copy is None:
            raise internal()
        context = self.world.environment(
            view, environment, subject, tuple(n for s in all_steps(plan) for n in s.needs.supplies)
        )
        mission = self.world.current_mission(view)
        # With no mission origin, guidance is unavailable rather than silently assuming a mission mode.
        steps = StepViews(
            self.world,
            view,
            copy,
            plan,
            context,
            None if mission is None else mission.mission_mode,
            session.kind in ("crew", "station"),
            view.release.release_id,
        )
        stages = [
            self._stage(view, copy, plan, context, steps, s.stage_id, s.steps)
            for s in view_stages(plan, context)
        ]
        review = copy.text("review_state_words", plan.review_state)
        # R28-2 is pending: the example notice supplies the review words until ui-copy has them.
        # Only the notice's first clause: a notice written in sentences ends it with a period, not a colon.
        review = review or doc.notice.partition(":")[0].partition(".")[0].lower()
        label = plan.condition_id or plan.flow_id or plan.plan_id
        tag = text(copy, "plan_lines", "plan_tag")
        if "{review_state}" not in tag:
            label += " · " + review
        wire: dict[str, object] = {
            "plan_id": plan.plan_id,
            "title": plan.title,
            "condition_id": plan.condition_id,
            "flow_id": plan.flow_id,
            "written_for": plan.written_for,
            "mode": plan.mode,
            "mode_text": text(copy, "plan_mode_words", plan.mode),
            "tag_text": copy.render(tag, {"plan_label": label, "review_state": review}),
            "environment_id": environment,
            "release_id": view.release.release_id,
            "plan_version": doc.document_sha256,
            "notice": doc.notice,
            "review_state": plan.review_state,
            "stages": stages,
            "plan_aboard": plan_aboard(plan, context),
            "waits_for_medical_officer": waits_for_medical_officer(plan, context),
            # Coordinator decision against C22: a return option that is null or missing is unknown, never
            # "no return" (AGENTS 1 rule 5), so the slot says the designed unknown state.
            "no_return_text": text(copy, "designed_states", "unknown")
            if not return_applies(plan, context)
            and (plan.return_stage is not None or not context.return_option_present)
            else None,
            "as_of": instant(view.now),
        }

        return PlanProjection(plan, wire, steps.runnable)

    def _stage(
        self,
        view: View,
        copy: UiCopy,
        plan: Plan,
        context: EnvContext,
        steps: StepViews,
        stage_id: str,
        members: tuple[object, ...],
    ) -> dict[str, object]:
        result: dict[str, object] = {"stage_id": stage_id, "label": text(copy, "plan_stage_words", stage_id)}
        if stage_id == "ground_log":
            line = self.world.ground_line or GroundLine(None, None)
            words, leaves = line.line(copy, view.now)
            result.update(
                blocking=False,
                row_text=text(copy, "plan_lines", "ground_log_row"),
                items=[{"text": i.text, "source_ids": list(i.source_ids)} for i in plan.ground_log_items],
                link_text=words,
                leaves_at=instant(leaves),
            )
        elif stage_id == "return" and plan.return_stage is not None:
            returning = plan.return_stage
            profiles = read_document(view.release, ConfigName.ENVIRONMENT_PROFILES)
            profile = next(
                object_value(v)
                for v in array_value((profiles or {})["profiles"])
                if object_value(v)["environment_id"] == context.environment_id
            )
            option = object_value(profile["return_option"])
            result.update(
                criteria=[
                    {"text": c.text, "source_ids": list(c.source_ids), "to_be_set_by": c.to_be_set_by}
                    for c in returning.criteria
                ],
                criteria_text=text(copy, "plan_lines", "return_criteria_pending")
                if any(c.to_be_set_by for c in returning.criteria)
                else None,
                prepare=[steps.step(s) for s in returning.prepare if s in members],
                decided_by=list(returning.decided_by),
                who_decides_text=text(copy, "plan_lines", "who_decides"),
                trip_text=option["trip_text"],
                trip_source_ids=option["source_ids"],
                rtdc=None if returning.rtdc_json is None else json.loads(returning.rtdc_json),
            )
        else:
            result["steps"] = [steps.step(s) for s in plan.steps if s in members]
        return result
