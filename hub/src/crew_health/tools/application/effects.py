from __future__ import annotations

from pydantic import ValidationError

from crew_health.contracts_gen.schemas.model_action_schema import ModelAction, ModelAction2
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.measurement.application.ports import MeasurementCommands, ProposalInput
from crew_health.tools.application.ports import AppliedEffects, EffectValidation, ScopeAuthority
from crew_health.tools.domain.arguments import parse_json
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.scope import ToolScope, require_own_subject
from crew_health.tools.domain.tool_result import PendingEffect
from crew_health.tools.settings import argument_bounds


class Effects:
    """Apply only inside E13's task completion UOW and its mission-life command receipt."""

    def __init__(
        self,
        measurements: MeasurementCommands | None,
        authority: ScopeAuthority,
        *,
        settings: HubSettings,
        validator: EffectValidation | None,
    ) -> None:
        self._bounds = argument_bounds(settings)
        self._maximum = settings.integer("tools.max_effects_per_turn")
        self._measurements, self._authority, self._validator = measurements, authority, validator

    def _proposal(self, uow: UnitOfWork, scope: ToolScope, pending: PendingEffect) -> ProposalInput:
        if (
            pending.turn_id != scope.turn_id
            or pending.source_revision != scope.source_revision
            or pending.generation != scope.generation
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        try:
            action = ModelAction.model_validate(parse_json(pending.arguments, self._bounds), strict=True)
        except ValidationError as error:
            raise ToolsInvalid("MALFORMED_MODEL_OUTPUT") from error
        value = action.root
        if (
            not isinstance(value, ModelAction2)
            or value.decision_affected is None
            or value.state_snapshot_version != scope.source_revision
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if self._validator is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        self._validator.validate(uow, scope, action)
        region = None if value.body_region is None else value.body_region.root
        return ProposalInput(
            scope.episode_id,
            value.capability_code.root,
            value.reason,
            value.decision_affected.root,
            None,
            None if region is None else region.root,
            True,
            () if value.condition_ids is None else tuple(item.root for item in value.condition_ids),
        )

    def apply(
        self,
        uow: UnitOfWork,
        scope: ToolScope,
        effects: tuple[PendingEffect, ...],
        idempotency_prefix: str,
    ) -> AppliedEffects:
        require_own_subject(scope.session, scope.subject_person_id)
        subject = scope.subject_person_id
        if (
            subject is None
            or idempotency_prefix != scope.turn_id
            or scope.config_release_id != uow.release.release_id
            or uow.actor != scope.session.actor
            or not self._authority.require_current(uow, scope)
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if len(effects) > self._maximum:
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        if len(effects) > 1:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if not effects:
            return AppliedEffects((), (), ())
        owner = self._measurements
        if owner is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        proposals = tuple(self._proposal(uow, scope, effect) for effect in effects)
        requests: list[str] = []
        gaps: list[str] = []
        results = []
        with uow.savepoint("tools_effects"):
            for proposal in proposals:
                if not self._authority.require_current(uow, scope):
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                result = owner.propose(
                    uow,
                    requester=Actor("assistant", scope.turn_id),
                    subject_person_id=subject,
                    proposal=proposal,
                )
                require_id(result.request_id)
                if result.state not in {"proposed", "unavailable"} or (
                    (result.refusal is not None) != (result.state == "unavailable")
                ):
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                if scope.episode_id is not None and result.state == "unavailable" and result.gap_id is None:
                    raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
                requests.append(result.request_id)
                results.append(result)
                if result.gap_id is not None:
                    require_id(result.gap_id)
                    gaps.append(result.gap_id)
        return AppliedEffects(tuple(requests), tuple(gaps), tuple(results))
