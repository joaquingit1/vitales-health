import re
from dataclasses import dataclass

from crew_health.routine.domain.cadence import period_days
from crew_health.routine.domain.errors import Finding as Finding
from crew_health.routine.domain.misses import window_for
from crew_health.routine.domain.release import (
    AddOccurrences,
    AddQuestions,
    AnswerOn,
    Binding,
    EnvironmentOn,
    MissionEventOn,
    ReplaceCadence,
    RoutineItem,
    RoutineRelease,
    UntilMissionEvent,
    WarningRaisedOn,
)


@dataclass(frozen=True, slots=True)
class Bounds:
    max_items: int
    max_bindings: int
    max_slots_per_binding: int


def _guidance(item: RoutineItem, pointer: str) -> list[Finding]:
    findings: list[Finding] = []
    plain = item.plain.strip()
    if not plain or plain[-1] not in ".?!" or re.search(r"[.?!]\s+\S", plain):
        findings.append(Finding("error", pointer + "/plain", "G1: plain must be one sentence"))
    if not any(s.strip() for s in item.steps):
        findings.append(Finding("error", pointer + "/steps", "G2: at least one nonblank step is required"))
    if not item.instrument.strip():
        findings.append(Finding("error", pointer + "/instrument", "G3: instrument is required"))
    regions = frozenset(
        (
            "head",
            "eyes",
            "ear",
            "neck",
            "shoulder",
            "heart",
            "lungs",
            "abdomen",
            "flank",
            "back",
            "wrist",
            "hand",
            "legs",
            "knee",
            "ankle",
        )
    )
    if item.body_region is not None and item.body_region not in regions:
        findings.append(Finding("error", pointer + "/body_region", "G4: unknown body region"))
    for i, step in enumerate(item.steps):
        if re.search(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|ug|µg|g|ml|mL|IU|units?)\b", step):
            findings.append(
                Finding("error", f"{pointer}/steps/{i}", "G5: amounts are forbidden in routine steps")
            )
    return findings


def _item_checks(item: RoutineItem, release: RoutineRelease, pointer: str) -> list[Finding]:
    findings = _guidance(item, pointer)
    for i, source in enumerate(item.source_ids):
        if source not in release.sources:
            findings.append(Finding("error", f"{pointer}/source_ids/{i}", "Unknown source"))
    for trigger in item.triggers:
        for i, source in enumerate(trigger.source_ids):
            if source not in release.sources:
                findings.append(
                    Finding(
                        "error",
                        f"{pointer}/raise_frequency_when/{trigger.index}/source_ids/{i}",
                        "Unknown source",
                    )
                )
        if trigger.basis == "team default":
            findings.append(
                Finding(
                    "warning",
                    f"{pointer}/raise_frequency_when/{trigger.index}/basis",
                    "G8: team default cadence",
                )
            )
    if item.cadence_basis == "team default":
        findings.append(Finding("warning", pointer + "/cadence/basis", "G8: team default cadence"))
    cadences = (item.default_cadence, *item.by_environment.values())
    if any(c.word == "per_eva" for c in cadences) and release.policy(item.item_id) is None:
        findings.append(Finding("warning", pointer + "/cadence", "per_eva has no item policy"))
    for c in (*cadences, *(t.new_cadence for t in item.triggers)):
        if release.group_for(c.word) is None:
            findings.append(
                Finding("error", pointer + "/cadence", f"Cadence word has no display group: {c.word}")
            )
    periods = [p for c in cadences if (p := period_days(c, release.calendar)) is not None]
    if periods:
        findings.append(
            Finding(
                "warning",
                pointer + "/cadence",
                f"Misses window w(I) = {window_for(release.misses, max(periods))} days",
            )
        )
    return findings


def _binding_shape(binding: Binding, release: RoutineRelease, pointer: str, bounds: Bounds) -> list[Finding]:
    findings: list[Finding] = []
    item = release.item(binding.item_id)
    if item is None:
        findings.append(Finding("error", pointer + "/binds/item_id", "Unknown item"))
    elif binding.kind == "trigger":
        trigger = next((t for t in item.triggers if t.index == binding.trigger_index), None)
        if trigger is None:
            findings.append(Finding("error", pointer + "/binds/trigger_index", "Unknown trigger index"))
        else:
            if binding.trigger_text != trigger.text:
                findings.append(Finding("error", pointer + "/binds/trigger_text", "Trigger text drift"))
            if isinstance(binding.effect, ReplaceCadence) and binding.effect.cadence != trigger.new_cadence:
                findings.append(Finding("error", pointer + "/effect/cadence", "Trigger cadence drift"))
    if binding.kind == "trigger" and isinstance(binding.on, EnvironmentOn):
        findings.append(Finding("error", pointer + "/on", "Environment activation cannot create an override"))
    if binding.audience == "person" and isinstance(binding.on, MissionEventOn | EnvironmentOn):
        findings.append(Finding("error", pointer + "/audience", "Binding input has no person"))
    slots = (
        len(binding.effect.times)
        if isinstance(binding.effect, AddOccurrences)
        else len(binding.effect.repeat_after_minutes)
        if isinstance(binding.effect, AddQuestions)
        else 0
    )
    if slots > bounds.max_slots_per_binding:
        findings.append(Finding("error", pointer + "/effect", "Binding slots exceed bound"))
    if isinstance(binding.effect, ReplaceCadence):
        cadence = binding.effect.cadence
        if release.group_for(cadence.word) is None:
            findings.append(
                Finding("error", pointer + "/effect/cadence/word", "Cadence word has no display group")
            )
        new_period = period_days(cadence, release.calendar)
        if item is not None and new_period is not None:
            bases = (item.default_cadence, *item.by_environment.values())
            if any(
                p is not None and new_period > p
                for c in bases
                if (p := period_days(c, release.calendar)) is not None
            ):
                findings.append(
                    Finding("warning", pointer + "/effect/cadence", "Looser periodic cadence is additive")
                )
    return findings


def _binding_references(
    binding: Binding,
    release: RoutineRelease,
    pointer: str,
    warning_ids: frozenset[str] | None,
    event_types: frozenset[str] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    if (
        isinstance(binding.effect, AddQuestions)
        and release.question_sets is not None
        and binding.effect.question_set_id not in release.question_sets
    ):
        findings.append(Finding("error", pointer + "/effect/question_set_id", "Unknown question set"))
    if (
        isinstance(binding.on, AnswerOn)
        and release.question_ids is not None
        and binding.on.question_id not in release.question_ids
    ):
        findings.append(Finding("error", pointer + "/on/question_id", "Unknown question"))
    if isinstance(binding.on, WarningRaisedOn) and warning_ids is not None:
        for i, definition_id in enumerate(binding.on.definition_ids):
            if definition_id not in warning_ids:
                findings.append(
                    Finding("error", f"{pointer}/on/definition_ids/{i}", "Unknown warning definition")
                )
    if event_types is not None:
        for key, part in (("on", binding.on), ("until", binding.until)):
            if isinstance(part, MissionEventOn | UntilMissionEvent):
                for i, event_type in enumerate(part.event_types):
                    if event_type not in event_types:
                        findings.append(
                            Finding("error", f"{pointer}/{key}/event_types/{i}", "Unknown mission event type")
                        )
    return findings


def check_release(
    release: RoutineRelease,
    bounds: Bounds,
    warning_definition_ids: frozenset[str] | None,
    event_types: frozenset[str] | None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    if len(release.items) > bounds.max_items:
        findings.append(Finding("error", "/items", "Item count exceeds bound"))
    if len(release.bindings) > bounds.max_bindings:
        findings.append(Finding("error", "/bindings", "Binding count exceeds bound"))
    seen_bindings: set[str] = set()
    for i, binding in enumerate(release.bindings):
        pointer = f"/bindings/{i}"
        if binding.binding_id in seen_bindings:
            findings.append(Finding("error", pointer + "/binding_id", "Duplicate binding id"))
        seen_bindings.add(binding.binding_id)
        findings.extend(_binding_shape(binding, release, pointer, bounds))
        findings.extend(_binding_references(binding, release, pointer, warning_definition_ids, event_types))
    seen_policies: set[str] = set()
    for i, policy in enumerate(release.policies.values()):
        if policy.item_id in seen_policies:
            findings.append(Finding("error", f"/item_policies/{i}/item_id", "Duplicate item policy"))
        seen_policies.add(policy.item_id)
        if release.item(policy.item_id) is None:
            findings.append(Finding("error", f"/item_policies/{i}/item_id", "Unknown item"))
    seen_words: set[str] = set()
    for i, (_, words) in enumerate(release.display_groups):
        for word in words:
            if word in seen_words:
                findings.append(
                    Finding("error", f"/display_groups/{i}/cadence_words", "Cadence word listed twice")
                )
            seen_words.add(word)
    for i, item in enumerate(release.items):
        findings.extend(_item_checks(item, release, f"/items/{i}"))
    if len({b.item_id for b in release.bindings if isinstance(b.effect, AddQuestions)}) > 1:
        findings.append(Finding("error", "/bindings", "Question bindings require one carrier item"))
    if release.question_ids is not None and release.misses.question_id not in release.question_ids:
        findings.append(Finding("error", "/misses/question_id", "Unknown barrier question"))
    return tuple(sorted(findings, key=lambda f: (f.pointer, f.severity, f.message)))
