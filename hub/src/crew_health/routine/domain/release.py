from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, cast, get_args

from crew_health.kernel.commands import canonical_json
from crew_health.routine.domain.errors import ConfigInvalid, Finding
from crew_health.routine.domain.vocabulary import Audience, CadenceWord, Overlay


@dataclass(frozen=True, slots=True)
class Cadence:
    word: CadenceWord
    n: int | None
    event: str | None
    phase: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class Trigger:
    index: int
    text: str
    new_cadence: Cadence
    source_ids: tuple[str, ...]
    basis: str


@dataclass(frozen=True, slots=True)
class RoutineItem:
    item_id: str
    name: str
    plain: str
    why: str
    who: tuple[str, ...]
    instrument: str
    minutes: int
    steps: tuple[str, ...]
    body_region: str | None
    default_cadence: Cadence
    by_environment: Mapping[str, Cadence]
    cadence_basis: str
    triggers: tuple[Trigger, ...]
    source_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    position: int

    def cadence_for(self, environment_id: str) -> Cadence:
        return self.by_environment.get(environment_id, self.default_cadence)


@dataclass(frozen=True, slots=True)
class Calendar:
    week_days: int
    month_days: int
    anchor: Literal["environment_start", "mission_start"]
    basis: str


@dataclass(frozen=True, slots=True)
class ItemPolicy:
    item_id: str
    preferred_minute: int | None
    window_minutes: int | None
    per_eva: Literal["before_start", "after_end", "both"] | None
    basis: str


@dataclass(frozen=True, slots=True)
class MissesPolicy:
    ask_barrier_after_misses: int
    window_days: int
    question_id: str
    basis: str


@dataclass(frozen=True, slots=True)
class WarningRaisedOn:
    definition_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MissionEventOn:
    event_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverlayOn:
    edge: Literal["ended", "planned"]  # internal enum
    overlay: Overlay


@dataclass(frozen=True, slots=True)
class AnswerOn:
    question_id: str
    presences: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationOn:
    code: str
    comparator: Literal["above", "at_or_above", "below", "at_or_below"]
    value: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class EnvironmentOn:
    environments: tuple[str, ...]


type BindingOn = WarningRaisedOn | MissionEventOn | OverlayOn | AnswerOn | ObservationOn | EnvironmentOn


@dataclass(frozen=True, slots=True)
class ReplaceCadence:
    cadence: Cadence


@dataclass(frozen=True, slots=True)
class AddOccurrences:
    at_trigger: bool
    times: tuple[tuple[int, int], ...]
    every_minutes: int | None
    at_clear: bool
    basis: str


@dataclass(frozen=True, slots=True)
class AddQuestions:
    question_set_id: str
    attach_to: Literal["next_occurrence", "each_occurrence"]
    repeat_after_minutes: tuple[int, ...]
    basis: str


type BindingEffect = ReplaceCadence | AddOccurrences | AddQuestions


@dataclass(frozen=True, slots=True)
class UntilWarningCleared:
    pass


@dataclass(frozen=True, slots=True)
class UntilEffectComplete:
    pass


@dataclass(frozen=True, slots=True)
class UntilDays:
    days: int


@dataclass(frozen=True, slots=True)
class UntilPersonStops:
    role: str


@dataclass(frozen=True, slots=True)
class UntilMissionEvent:
    event_types: tuple[str, ...]


type BindingUntil = (
    UntilWarningCleared | UntilEffectComplete | UntilDays | UntilPersonStops | UntilMissionEvent
)


@dataclass(frozen=True, slots=True)
class Binding:
    binding_id: str
    kind: Literal["trigger", "cadence_event"]
    item_id: str
    trigger_index: int | None
    trigger_text: str | None
    event_text: str | None
    environment_id: str | None
    on: BindingOn
    effect: BindingEffect
    until: BindingUntil
    audience: Audience
    basis: str


@dataclass(frozen=True, slots=True)
class RoutineWords:
    cadence_words: Mapping[str, str]
    group_labels: Mapping[str, str]
    end_text: Mapping[str, str]
    skip_reasons: Mapping[str, str]
    overlay_words: Mapping[str, str]
    unknown: str


@dataclass(frozen=True, slots=True)
class RoutineRelease:
    release_id: str
    notice: str
    items: tuple[RoutineItem, ...]
    sources: Mapping[str, str]
    calendar: Calendar
    suppressed_overlays: tuple[Overlay, ...]
    display_groups: tuple[tuple[str, tuple[CadenceWord, ...]], ...]
    policies: Mapping[str, ItemPolicy]
    misses: MissesPolicy
    bindings: tuple[Binding, ...]
    question_sets: Mapping[str, tuple[str, ...]] | None
    words: RoutineWords | None
    item_content_sha256: Mapping[str, str]
    # Retain the questionnaire catalog for cross-document checks, including the barrier question.
    question_ids: frozenset[str] | None = None

    def item(self, item_id: str) -> RoutineItem | None:
        return next((item for item in self.items if item.item_id == item_id), None)

    def policy(self, item_id: str) -> ItemPolicy | None:
        return self.policies.get(item_id)

    def group_for(self, word: CadenceWord) -> str | None:
        return next((group for group, words in self.display_groups if word in words), None)

    def bindings_for_item(self, item_id: str) -> tuple[Binding, ...]:
        return tuple(b for b in self.bindings if b.item_id == item_id)

    def question_carrier_item_id(self) -> str | None:
        ids = frozenset(b.item_id for b in self.bindings if isinstance(b.effect, AddQuestions))
        return next(iter(ids)) if len(ids) == 1 else None


def _invalid(pointer: str, message: str) -> ConfigInvalid:
    return ConfigInvalid((Finding("error", pointer, message),))


def _mapping(value: object, pointer: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise _invalid(pointer, "Expected an object")
    return cast(Mapping[str, object], value)


def _array(value: object, pointer: str) -> list[object]:
    if not isinstance(value, list):
        raise _invalid(pointer, "Expected an array")
    return cast(list[object], value)


def _string(value: object, pointer: str) -> str:
    if not isinstance(value, str):
        raise _invalid(pointer, "Expected a string")
    return value


def _integer(value: object, pointer: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(pointer, "Expected an integer")
    return value


def _boolean(value: object, pointer: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid(pointer, "Expected a boolean")
    return value


def _choice(value: object, choices: tuple[object, ...], pointer: str) -> str:
    text = _string(value, pointer)
    if text not in choices:
        raise _invalid(pointer, "Unknown enum value")
    return text


@dataclass(frozen=True, slots=True)
class _Reader:
    data: Mapping[str, object]
    pointer: str

    def raw(self, key: str) -> object:
        if key not in self.data:
            raise _invalid(self.path(key), "Missing required field")
        return self.data[key]

    def path(self, key: str) -> str:
        return self.pointer + "/" + key.replace("~", "~0").replace("/", "~1")

    def text(self, key: str) -> str:
        return _string(self.raw(key), self.path(key))

    def optional_text(self, key: str) -> str | None:
        value = self.data.get(key)
        return None if value is None else _string(value, self.path(key))

    def integer(self, key: str) -> int:
        return _integer(self.raw(key), self.path(key))

    def nullable_text(self, key: str) -> str | None:
        value = self.raw(key)
        return None if value is None else _string(value, self.path(key))

    def nullable_int(self, key: str) -> int | None:
        value = self.raw(key)
        return None if value is None else _integer(value, self.path(key))

    def strings(self, key: str) -> tuple[str, ...]:
        return tuple(
            _string(v, f"{self.path(key)}/{i}") for i, v in enumerate(_array(self.raw(key), self.path(key)))
        )

    def objects(self, key: str) -> tuple[_Reader, ...]:
        return tuple(
            _Reader(_mapping(v, f"{self.path(key)}/{i}"), f"{self.path(key)}/{i}")
            for i, v in enumerate(_array(self.raw(key), self.path(key)))
        )

    def child(self, key: str) -> _Reader:
        return _Reader(_mapping(self.raw(key), self.path(key)), self.path(key))

    def choice(self, key: str, choices: tuple[object, ...]) -> str:
        return _choice(self.raw(key), choices, self.path(key))


def _cadence(r: _Reader) -> Cadence:
    word = cast(CadenceWord, r.choice("word", get_args(CadenceWord)))
    n = r.nullable_int("n")
    if word == "every_n_days" and (n is None or n < 1):
        raise _invalid(r.path("n"), "A positive period is required")
    return Cadence(word, n, r.optional_text("event"), r.optional_text("phase"), r.optional_text("note"))


def _item(r: _Reader, position: int) -> RoutineItem:
    c = r.child("cadence")
    env = c.child("by_environment")
    by_env = {k: _cadence(env.child(k)) for k in env.data}
    triggers = tuple(
        Trigger(
            i, t.text("trigger"), _cadence(t.child("new_cadence")), t.strings("source_ids"), t.text("basis")
        )
        for i, t in enumerate(r.objects("raise_frequency_when"))
    )
    skills = r.strings("skill_ids") if "skill_ids" in r.data else ()
    return RoutineItem(
        r.text("id"),
        r.text("name"),
        r.text("plain"),
        r.text("why"),
        r.strings("who"),
        r.text("instrument"),
        r.integer("minutes"),
        r.strings("steps"),
        r.nullable_text("body_region"),
        _cadence(c.child("default")),
        MappingProxyType(by_env),
        c.text("basis"),
        triggers,
        r.strings("source_ids"),
        skills,
        position,
    )


def _minute(value: str, pointer: str) -> int:
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
        raise _invalid(pointer, "Expected HH:MM")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _policy(r: _Reader) -> ItemPolicy:
    time = r.nullable_text("preferred_time")
    per_eva = r.nullable_text("per_eva")
    if per_eva is not None:
        _choice(per_eva, ("before_start", "after_end", "both"), r.path("per_eva"))
    return ItemPolicy(
        r.text("item_id"),
        None if time is None else _minute(time, r.path("preferred_time")),
        r.nullable_int("window_minutes"),
        cast(Literal["before_start", "after_end", "both"] | None, per_eva),
        r.text("basis"),
    )


def _on(r: _Reader) -> BindingOn:
    kind = r.text("kind")
    if kind == "warning_raised":
        return WarningRaisedOn(r.strings("definition_ids"))
    if kind == "mission_event":
        return MissionEventOn(r.strings("event_types"))
    if kind in ("overlay_ended", "overlay_planned"):
        edge: Literal["ended", "planned"] = "ended" if kind == "overlay_ended" else "planned"  # internal enum
        return OverlayOn(edge, cast(Overlay, r.choice("overlay", get_args(Overlay))))
    if kind == "answer":
        return AnswerOn(r.text("question_id"), r.strings("presence"))
    if kind == "environment_activated":
        return EnvironmentOn(r.strings("environments"))
    if kind == "observation":
        value = r.raw("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not Decimal(str(value)).is_finite()
        ):
            raise _invalid(r.path("value"), "Expected a finite number")
        comparator = cast(
            Literal["above", "at_or_above", "below", "at_or_below"],
            r.choice("comparator", ("above", "at_or_above", "below", "at_or_below")),
        )
        return ObservationOn(r.text("code"), comparator, Decimal(str(value)), r.text("unit"))
    raise _invalid(r.path("kind"), "Unknown binding input")


def _effect(r: _Reader) -> BindingEffect:
    kind = r.text("kind")
    if kind == "replace_cadence":
        return ReplaceCadence(_cadence(r.child("cadence")))
    if kind == "add_occurrences":
        times = tuple(
            (t.integer("day_offset"), _minute(t.text("local_time"), t.path("local_time")))
            for t in r.objects("times")
        )
        return AddOccurrences(
            _boolean(r.raw("at_trigger"), r.path("at_trigger")),
            times,
            r.nullable_int("every_minutes"),
            _boolean(r.raw("at_clear"), r.path("at_clear")),
            r.text("basis"),
        )
    if kind == "add_questions":
        repeats = tuple(
            _integer(v, f"{r.path('repeat_after_minutes')}/{i}")
            for i, v in enumerate(_array(r.raw("repeat_after_minutes"), r.path("repeat_after_minutes")))
        )
        attach = cast(
            Literal["next_occurrence", "each_occurrence"],
            r.choice("attach_to", ("next_occurrence", "each_occurrence")),
        )
        return AddQuestions(r.text("question_set_id"), attach, repeats, r.text("basis"))
    raise _invalid(r.path("kind"), "Unknown binding effect")


def _until(r: _Reader) -> BindingUntil:
    kind = r.text("kind")
    if kind == "warning_cleared":
        return UntilWarningCleared()
    if kind == "effect_complete":
        return UntilEffectComplete()
    if kind == "days":
        return UntilDays(r.integer("days"))
    if kind == "person_stops":
        return UntilPersonStops(r.text("role"))
    if kind == "mission_event":
        return UntilMissionEvent(r.strings("event_types"))
    raise _invalid(r.path("kind"), "Unknown binding end")


def _binding(r: _Reader) -> Binding:
    b = r.child("binds")
    kind = cast(Literal["trigger", "cadence_event"], b.choice("kind", ("trigger", "cadence_event")))
    index = b.integer("trigger_index") if kind == "trigger" else None
    text = b.text("trigger_text") if kind == "trigger" else None
    event = b.text("event_text") if kind == "cadence_event" else None
    return Binding(
        r.text("binding_id"),
        kind,
        b.text("item_id"),
        index,
        text,
        event,
        b.nullable_text("environment_id") if kind == "cadence_event" else None,
        _on(r.child("on")),
        _effect(r.child("effect")),
        _until(r.child("until")),
        cast(Audience, r.choice("audience", get_args(Audience))),
        r.text("basis"),
    )


def _words(data: Mapping[str, object] | None) -> RoutineWords | None:
    if data is None:
        return None
    r = _Reader(_mapping(data, "/ui-copy"), "/ui-copy")
    maps = []
    for key in (
        "cadence_words",
        "display_group_labels",
        "occurrence_end_text",
        "skip_reasons",
        "overlay_words",
    ):
        child = r.child(key)
        maps.append(MappingProxyType({k: child.text(k) for k in child.data}))
    return RoutineWords(
        maps[0], maps[1], maps[2], maps[3], maps[4], r.child("designed_states").text("unknown")
    )


def _questions(
    data: Mapping[str, object] | None,
) -> tuple[Mapping[str, tuple[str, ...]] | None, frozenset[str] | None]:
    if data is None:
        return None, None
    r = _Reader(_mapping(data, "/questionnaires"), "/questionnaires")
    return MappingProxyType(
        {q.text("id"): q.strings("question_ids") for q in r.objects("question_sets")}
    ), frozenset(q.text("id") for q in r.objects("questions"))


def _policies(r: _Reader) -> Mapping[str, ItemPolicy]:
    policies: dict[str, ItemPolicy] = {}
    for entry in r.objects("item_policies"):
        policy = _policy(entry)
        if policy.item_id in policies:
            raise _invalid(entry.path("item_id"), "Duplicate item policy")
        policies[policy.item_id] = policy
    return MappingProxyType(policies)


def _suppressed(r: _Reader) -> tuple[Overlay, ...]:
    overlays = r.raw("suppressed_overlays")
    # Also accept the schema variant that wraps the list in an overlays object.
    if isinstance(overlays, Mapping):
        overlays = _Reader(_mapping(overlays, "/suppressed_overlays"), "/suppressed_overlays").raw("overlays")
    return tuple(
        cast(Overlay, _choice(v, get_args(Overlay), f"/suppressed_overlays/{i}"))
        for i, v in enumerate(_array(overlays, "/suppressed_overlays"))
    )


def _content_digest(item: RoutineItem) -> str:
    content = {
        "plain": item.plain,
        "why": item.why,
        "instrument": item.instrument,
        "minutes": item.minutes,
        "steps": list(item.steps),
        "body_region": item.body_region,
    }
    try:
        return sha256(canonical_json(content)).hexdigest()
    except ValueError as error:
        raise _invalid(f"/items/{item.position}", "Content cannot be canonicalized") from error


def parse_release(
    release_id: str,
    schedule: Mapping[str, object],
    rules: Mapping[str, object],
    ui_copy: Mapping[str, object] | None,
    questionnaires: Mapping[str, object] | None,
) -> RoutineRelease:
    s, r = _Reader(_mapping(schedule, ""), ""), _Reader(_mapping(rules, ""), "")
    items = tuple(_item(v, i) for i, v in enumerate(s.objects("items")))
    c, m = r.child("calendar"), r.child("misses")
    calendar = Calendar(
        c.integer("week_days"),
        c.integer("month_days"),
        cast(
            Literal["environment_start", "mission_start"],
            c.choice("anchor", ("environment_start", "mission_start")),
        ),
        c.text("basis"),
    )
    misses = MissesPolicy(
        m.integer("ask_barrier_after_misses"),
        m.integer("window_days"),
        m.text("question_id"),
        m.text("basis"),
    )
    suppressed = _suppressed(r)
    groups = tuple(
        (
            g.text("id"),
            tuple(
                cast(CadenceWord, _choice(w, get_args(CadenceWord), g.path("cadence_words")))
                for w in g.strings("cadence_words")
            ),
        )
        for g in r.objects("display_groups")
    )
    question_sets, question_ids = _questions(questionnaires)
    digests = {item.item_id: _content_digest(item) for item in items}
    return RoutineRelease(
        release_id,
        r.text("notice"),
        items,
        MappingProxyType({v.text("id"): v.text("title") for v in s.objects("sources")}),
        calendar,
        suppressed,
        groups,
        _policies(r),
        misses,
        tuple(_binding(b) for b in r.objects("bindings")),
        question_sets,
        _words(ui_copy),
        MappingProxyType(digests),
        question_ids,
    )
