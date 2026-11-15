from __future__ import annotations

from dataclasses import asdict

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import UnitOfWork
from crew_health.tools.application.ports import (
    AuthorizedContextPort,
    ContextIdentity,
    PromptSerializer,
    ScopeAuthority,
    TokenCounter,
)
from crew_health.tools.domain.context import (
    ContextBudget,
    ContextSection,
    ContextSnapshot,
    require_context_budget,
)
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope, require_own_subject
from crew_health.tools.domain.support import SupportItem
from crew_health.tools.settings import validate_settings


def _item(item: SupportItem, scope: ToolScope) -> dict[str, object]:
    quantity = item.quantity
    return {
        "ref_id": item.ref_id,
        "kind": item.kind,
        "origin_mode": item.origin_mode,
        "quality": item.quality,
        "freshness": item.freshness,
        "acquisition_method": item.acquisition_method,
        "presence": item.presence,
        "observed_at": None if item.observed_at is None else item.observed_at.isoformat(),
        "corrected_at": None if item.corrected_at is None else item.corrected_at.isoformat(),
        "quantity": None if quantity is None else {"value": str(quantity.value), "unit": quantity.unit.code},
        "release_id": item.release_id,
        "content_sha256": item.content_sha256,
        "exclusion_reason": item.exclusion_reason,
        "code": item.code,
        "question_id": item.question_id,
        "questionnaire_version": item.questionnaire_version,
        "question_text": item.question_text,
        "value": {"value": str(item.value.value), "unit": item.value.unit.code}
        if isinstance(item.value, Quantity)
        else item.value,
        "source_revision": item.source_revision,
        "observed": None
        if item.observed is None
        else {**asdict(item.observed), "original_at": item.observed.original_at.isoformat()},
        "normalized": None
        if item.normalized is None
        else {
            **asdict(item.normalized),
            "original_at": item.normalized.original_at.isoformat(),
            "corrected_at": None
            if item.normalized.corrected_at is None
            else item.normalized.corrected_at.isoformat(),
        },
        "freshness_policy_ref": item.freshness_policy_ref,
        "supersedes": item.supersedes,
        "conflicting_refs": item.conflicting_refs,
        "red_flag_ids": item.red_flag_ids,
        "permitted_uses": sorted(item.permitted_uses(mission_origin=scope.origin_mode)),
    }


class ContextBuilder:
    def __init__(
        self,
        projections: AuthorizedContextPort,
        tokens: TokenCounter | None,
        prompt: PromptSerializer,
        authority: ScopeAuthority,
        settings: HubSettings,
        *,
        identity: ContextIdentity,
    ) -> None:
        validate_settings(settings)
        self._projections, self._tokens, self._prompt = projections, tokens, prompt
        self._authority, self._settings, self._identity = authority, settings, identity

    def _serialize(self, scope: ToolScope, phase: Phase, sections: tuple[ContextSection, ...]) -> bytes:
        return canonical_json(
            [
                {"name": section.name, "items": [_item(item, scope) for item in section.items]}
                for section in sections
            ]
        )

    def _budget(self, scope: ToolScope, phase: Phase, raw: bytes, budget: ContextBudget) -> None:
        if self._tokens is None:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        prompt = self._prompt.serialize(scope, phase, raw)
        max_bytes = int(self._settings.quantity("tools.context.max_bytes").value)
        if len(prompt) > max_bytes:
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        count = self._tokens.count(prompt, model_release_id=scope.model_release_id)
        require_context_budget(
            prompt=prompt,
            prompt_tokens=count,
            output_reserve=budget.output_reserve,
            max_bytes=max_bytes,
            max_tokens=min(budget.max_tokens, self._settings.integer("tools.context.max_tokens")),
        )

    def build(
        self, uow: UnitOfWork, scope: ToolScope, phase: Phase, budget: ContextBudget
    ) -> ContextSnapshot:
        require_own_subject(scope.session, scope.subject_person_id)
        person_id = scope.subject_person_id
        if person_id is None or phase != scope.phase or scope.config_release_id != uow.release.release_id:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if not self._authority.require_current(uow, scope):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        sections = self._projections.sections(uow, scope, phase)
        checked = self._validate(sections, scope, person_id, budget, uow)
        required = tuple(section for section in checked if section.required)
        self._budget(scope, phase, self._serialize(scope, phase, required), budget)
        selected = list(required)
        omitted: list[str] = []
        for section in checked:
            if section.required:
                continue
            candidate = tuple(sorted((*selected, section), key=lambda s: budget.section_order.index(s.name)))
            try:
                self._budget(scope, phase, self._serialize(scope, phase, candidate), budget)
            except ToolsInvalid as error:
                if error.code != "PAYLOAD_TOO_LARGE":
                    raise
                omitted.append(section.name)
                continue
            selected = list(candidate)
        selected.sort(key=lambda s: budget.section_order.index(s.name))
        raw = self._serialize(scope, phase, tuple(selected))
        self._budget(scope, phase, raw, budget)
        if not self._authority.require_current(uow, scope):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        identity = canonical_json(
            {
                "scope": [
                    scope.turn_id,
                    scope.source_revision,
                    scope.generation,
                    scope.config_release_id,
                    scope.evidence_release_id,
                    scope.model_release_id,
                ],
                "sections": raw.decode("utf-8"),
            }
        )
        fingerprint = self._identity.fingerprint(
            owner_person_id=person_id, canonical_identity_and_body=identity
        )
        return ContextSnapshot(
            raw,
            tuple(item.ref_id for s in selected for item in s.items),
            tuple(omitted),
            scope.config_release_id,
            scope.evidence_release_id,
            scope.model_release_id,
            scope.source_revision,
            fingerprint,
        )

    def _validate(
        self,
        sections: tuple[ContextSection, ...],
        scope: ToolScope,
        person_id: str,
        budget: ContextBudget,
        uow: UnitOfWork,
    ) -> tuple[ContextSection, ...]:
        maximum = self._settings.integer("tools.context.max_sections")
        if len(sections) > maximum or len(budget.section_order) > maximum:
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        names = tuple(section.name for section in sections)
        if len(set(names)) != len(names) or set(names) != set(budget.section_order):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if sum(len(s.items) for s in sections) > self._settings.integer("tools.context.max_items"):
            raise ToolsInvalid("PAYLOAD_TOO_LARGE")
        identifiers = tuple(item.ref_id for section in sections for item in section.items)
        available = frozenset(identifiers)
        if len(available) != len(identifiers):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        if any(not set(item.conflicting_refs) <= available for section in sections for item in section.items):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
        material_refs = frozenset(
            ref for section in sections for item in section.items for ref in item.conflicting_refs
        )
        checked: list[ContextSection] = []
        for section in sorted(sections, key=lambda s: budget.section_order.index(s.name)):
            items = tuple(
                item.for_context(person_id=person_id, origin_mode=scope.origin_mode, at=uow.now)
                for item in section.items
            )
            if any(item.kind == "reading" for item in items) and section.name != "place_facts":
                raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
            required = section.required or any(
                item.kind in {"answer", "observation"}
                or item.presence == "absent"
                or item.red_flag_ids
                or item.conflicting_refs
                or item.required
                or item.ref_id in material_refs
                for item in items
            )
            checked.append(ContextSection(section.name, items, required))
        return tuple(checked)
