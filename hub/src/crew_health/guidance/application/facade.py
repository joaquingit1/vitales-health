"""SYSTEM 8.2 facade: adapts every optional fact provider to GuidanceFacts and applies the pure rule.

A provider that is absent, raises, or answers outside its bound becomes a typed Unknown naming the provider; unknown
facts never authorize guidance. The configured substitute is resolved once, under the same snapshot and pin, with no
recursion (its own substitute is never followed).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from crew_health.guidance.application.ports import (
    ContentFacts,
    ContentFactSet,
    EnvironmentFacts,
    EquipmentFacts,
    Fact,
    GuidanceDecision,
    GuidanceFacts,
    GuidanceRequest,
    Known,
    OperatorFact,
    SkillFacts,
    StockFact,
    StockFacts,
    Unknown,
    apply_substitute,
    decide,
)
from crew_health.kernel.uow import ReadSnapshot

NO_PROVIDER = "no_provider"
PROVIDER_ERROR = "provider_error"
OVER_BOUND = "over_bound"


@dataclass(frozen=True, slots=True)
class GuidanceBounds:
    """Hub settings guidance.max_requirement_items and guidance.max_operators (SYSTEM 5.11)."""

    max_requirement_items: int
    max_operators: int


@dataclass(frozen=True, slots=True)
class FactProviders:
    content: ContentFacts | None = None
    environment: EnvironmentFacts | None = None
    equipment: EquipmentFacts | None = None
    skills: SkillFacts | None = None
    stock: StockFacts | None = None


def _ask[T](provider: str, call: Callable[[], Fact[T]]) -> Fact[T]:
    try:
        return call()
    except Exception:  # a failing optional provider degrades to Unknown, never to a crash or a default
        return Unknown(provider, PROVIDER_ERROR)


def _unknown_content(reason: str) -> ContentFactSet:
    missing = Unknown("content", reason)
    return ContentFactSet(
        source_and_hash_valid=missing,
        purpose_allowed=missing,
        clinical_review="not_clinically_reviewed",
        withdrawn=missing,
        simulation_only=missing,
        pinned_release_matches=False,
        environments=missing,
        substitute=None,
        substitute_requirements=None,
    )


class EligibilityFacade:
    def __init__(self, bounds: GuidanceBounds) -> None:
        if bounds.max_requirement_items < 1 or bounds.max_operators < 1:
            raise ValueError("guidance bounds must be positive")
        self._bounds = bounds
        self._providers = FactProviders()
        self._attached = False

    def attach(self, providers: FactProviders) -> None:
        """Late binding (SYSTEM 5.12): called once by the composition root with the optional providers found."""
        if self._attached:
            raise ValueError("guidance providers already attached")
        self._providers = providers
        self._attached = True

    def evaluate(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> GuidanceDecision:
        content = self._content(snapshot, request)
        primary = decide(request, self._facts(snapshot, request, content, follow_substitute=True))
        substitute = content.substitute
        requirements = content.substitute_requirements
        if substitute is None or requirements is None or primary.availability != "not_aboard":
            return primary
        sub_request = replace(request, content=substitute, requirements=requirements)
        sub_content = replace(
            self._content(snapshot, sub_request), substitute=None, substitute_requirements=None
        )
        sub_decision = decide(
            sub_request, self._facts(snapshot, sub_request, sub_content, follow_substitute=False)
        )
        return apply_substitute(primary, substitute, sub_decision)

    def _within_bounds(self, request: GuidanceRequest) -> bool:
        r = request.requirements
        limit = self._bounds.max_requirement_items
        return len(r.instruments) <= limit and len(r.supplies) <= limit and len(r.skills) <= limit

    def _content(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> ContentFactSet:
        provider = self._providers.content
        if not self._within_bounds(request):
            return _unknown_content(OVER_BOUND)
        if provider is None:
            return _unknown_content(NO_PROVIDER)
        try:
            return provider.content(
                snapshot, request.content, purpose=request.purpose, release_id=request.release_id
            )
        except Exception:  # see _ask
            return _unknown_content(PROVIDER_ERROR)

    def _facts(
        self,
        snapshot: ReadSnapshot,
        request: GuidanceRequest,
        content: ContentFactSet,
        *,
        follow_substitute: bool,
    ) -> GuidanceFacts:
        return GuidanceFacts(
            source_and_hash_valid=content.source_and_hash_valid,
            purpose_allowed=content.purpose_allowed,
            clinical_review=content.clinical_review,
            withdrawn=content.withdrawn,
            simulation_only=content.simulation_only,
            pinned_release_matches=content.pinned_release_matches,
            environment_applicable=self._environment(snapshot, request, content.environments),
            instruments_here=self._instruments(snapshot, request),
            stock=self._stock(snapshot, request),
            operators=self._operators(snapshot, request),
            substitute=content.substitute if follow_substitute else None,
        )

    def _environment(
        self, snapshot: ReadSnapshot, request: GuidanceRequest, environments: Fact[frozenset[str]]
    ) -> Fact[bool]:
        provider = self._providers.environment
        if isinstance(environments, Unknown):
            return environments
        if provider is None:
            return Unknown("environment", NO_PROVIDER)
        envs = environments.value
        return _ask("environment", lambda: provider.applicable(snapshot, request.patient_location_id, envs))

    def _instruments(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> Fact[frozenset[str]]:
        provider = self._providers.equipment
        if not request.requirements.instruments:
            return Known(frozenset())
        if provider is None:
            return Unknown("equipment", NO_PROVIDER)
        return _ask("equipment", lambda: provider.instruments_here(snapshot, request.patient_location_id))

    def _stock(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> Mapping[str, Fact[StockFact]]:
        supplies = request.requirements.supplies
        provider = self._providers.stock
        if not supplies:
            return {}
        if provider is None:
            return {key: Unknown("stock", NO_PROVIDER) for key in sorted(supplies)}
        try:
            answer = provider.stock(snapshot, request.patient_location_id, supplies)
        except Exception:  # see _ask
            return {key: Unknown("stock", PROVIDER_ERROR) for key in sorted(supplies)}
        # Only the asked keys are read; a key the provider left out stays absent, which the rule counts as missing.
        return {key: answer[key] for key in sorted(supplies) if key in answer}

    def _operators(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> Fact[tuple[OperatorFact, ...]]:
        provider = self._providers.skills
        if provider is None:
            return Unknown("skills", NO_PROVIDER)
        answer = _ask(
            "skills",
            lambda: provider.operators(snapshot, request.subject_person_id, request.patient_location_id),
        )
        if isinstance(answer, Known) and len(answer.value) > self._bounds.max_operators:
            return Unknown("skills", OVER_BOUND)
        return answer
