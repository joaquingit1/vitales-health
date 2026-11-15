"""SQLite boundary codecs. Private notes remain only in their owning rows."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Literal, cast

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.storage import SqlValue
from crew_health.routine.domain.activation import CadenceActivation
from crew_health.routine.domain.instances import QuestionInstance
from crew_health.routine.domain.occurrence import Occurrence, Outcome, Skip
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import (
    AddOccurrences,
    AddQuestions,
    BindingEffect,
    Cadence,
    ReplaceCadence,
)
from crew_health.routine.domain.vocabulary import (
    Audience,
    CadenceWord,
    CauseKind,
    DueKind,
    EndReason,
    EndsKind,
    MemberBasis,
    OccurrenceState,
    OriginMode,
    SkipReason,
    SourceKind,
    StartedBy,
)


def instant(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def timestamp(value: SqlValue) -> datetime:
    return datetime.fromisoformat(text(value))


def optional_time(value: SqlValue) -> datetime | None:
    return None if value is None else timestamp(value)


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("routine stored field is not text")
    return value


def integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("routine stored field is not an integer")
    return value


def optional_text(value: object) -> str | None:
    return None if value is None else text(value)


def boolean(value: object) -> bool:
    number = integer(value)
    if number not in (0, 1):
        raise ValueError("routine stored boolean is invalid")
    return bool(number)


def dump(value: object) -> str:
    return canonical_json(value).decode("utf-8")


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("routine stored JSON is not an object")
    return cast(dict[str, object], value)


def load_map(value: SqlValue) -> dict[str, object]:
    return mapping(json.loads(text(value)))


def strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("routine stored JSON is not an array")
    return tuple(text(v) for v in value)


def load_strings(value: SqlValue) -> tuple[str, ...]:
    return strings(json.loads(text(value)))


def cadence(value: object) -> Cadence:
    data = mapping(value)
    n = data.get("n")
    return Cadence(
        cast(CadenceWord, text(data["word"])),
        None if n is None else integer(n),
        optional_text(data.get("event")),
        optional_text(data.get("phase")),
        optional_text(data.get("note")),
    )


def effect_json(effect: BindingEffect) -> str:
    if isinstance(effect, ReplaceCadence):
        return dump({"kind": "replace_cadence", "cadence": asdict(effect.cadence)})
    if isinstance(effect, AddQuestions):
        return dump({"kind": "add_questions", **asdict(effect)})
    return dump({"kind": "add_occurrences", **asdict(effect)})


def effect(value: SqlValue) -> BindingEffect:
    data = load_map(value)
    if data["kind"] == "replace_cadence":
        return ReplaceCadence(cadence(data["cadence"]))
    if data["kind"] == "add_questions":
        repeats = data["repeat_after_minutes"]
        if not isinstance(repeats, list):
            raise ValueError("routine stored repeats are invalid")
        return AddQuestions(
            text(data["question_set_id"]),
            cast(Literal["next_occurrence", "each_occurrence"], text(data["attach_to"])),
            tuple(integer(v) for v in repeats),
            text(data["basis"]),
        )
    if data["kind"] != "add_occurrences":
        raise ValueError("routine stored effect is invalid")
    times = data["times"]
    if not isinstance(times, list):
        raise ValueError("routine stored times are invalid")
    pairs: list[tuple[int, int]] = []
    for pair in times:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("routine stored time is invalid")
        pairs.append((integer(pair[0]), integer(pair[1])))
    every = data["every_minutes"]
    return AddOccurrences(
        data["at_trigger"] is True,
        tuple(pairs),
        None if every is None else integer(every),
        data["at_clear"] is True,
        text(data["basis"]),
    )


def occurrence_values(o: Occurrence) -> tuple[SqlValue, ...]:
    outcome = None if o.outcome is None else dump(asdict(o.outcome))
    skip = (
        None
        if o.skip is None
        else dump({"reason": o.skip.reason, "note": o.skip.note, "at": instant(o.skip.at)})
    )
    return (
        o.occurrence_id,
        o.revision,
        o.person_id,
        o.item_id,
        o.environment_id,
        o.mission_day,
        o.cause_kind,
        o.cause_key,
        int(o.periodic),
        o.override_id,
        o.binding_id,
        o.started_by,
        o.insight_id,
        o.state,
        instant(o.due_from),
        instant(o.base_due_until),
        instant(o.due_until),
        o.due_kind,
        instant(o.later_until),
        instant(o.completed_at),
        outcome,
        skip,
        None if o.miss_counted is None else int(o.miss_counted),
        o.end_reason,
        o.guidance_release_id,
        o.config_release_id,
        o.origin_mode,
        o.clock_version,
        instant(o.created_at),
    )


def read_occurrence(r: tuple[SqlValue, ...]) -> Occurrence:
    outcome = None
    if r[20] is not None:
        d = load_map(r[20])
        outcome = Outcome(strings(d["observation_ids"]), strings(d["answer_ids"]), optional_text(d["note"]))
    skip = None
    if r[21] is not None:
        d = load_map(r[21])
        skip = Skip(
            cast(SkipReason, text(d["reason"])),
            optional_text(d["note"]),
            datetime.fromisoformat(text(d["at"])),
        )
    return Occurrence(
        text(r[0]),
        integer(r[1]),
        text(r[2]),
        text(r[3]),
        text(r[4]),
        integer(r[5]),
        cast(CauseKind, text(r[6])),
        text(r[7]),
        boolean(r[8]),
        optional_text(r[9]),
        optional_text(r[10]),
        optional_text(r[11]),
        optional_text(r[12]),
        cast(OccurrenceState, text(r[13])),
        optional_time(r[14]),
        optional_time(r[15]),
        optional_time(r[16]),
        cast(DueKind, text(r[17])),
        optional_time(r[18]),
        optional_time(r[19]),
        outcome,
        skip,
        None if r[22] is None else boolean(r[22]),
        optional_text(r[23]),
        optional_text(r[24]),
        text(r[25]),
        cast(OriginMode, text(r[26])),
        text(r[27]),
        timestamp(r[28]),
    )


def override_values(o: Override) -> tuple[SqlValue, ...]:
    return (
        o.override_id,
        o.revision,
        o.item_id,
        o.person_id,
        o.audience,
        o.binding_id,
        o.trigger_index,
        o.trigger_text,
        o.started_by,
        o.source_kind,
        o.source_ref,
        dump(asdict(o.new_cadence)),
        o.cadence_text,
        o.reason_text,
        o.state,
        instant(o.started_at),
        o.ends_kind,
        instant(o.ends_at),
        o.ends_text,
        o.stop_role,
        instant(o.ended_at),
        o.end_reason,
        o.basis,
        dump(o.source_ids),
        o.config_release_id,
        dump(o.member_ids),
        dump(dict(o.member_basis)),
        o.source_origin_mode,
        instant(o.source_at),
        dump(o.source_chain),
        effect_json(o.effect),
        int(o.awaiting_disposition),
        o.note,
    )


def read_override(r: tuple[SqlValue, ...]) -> Override:
    basis = {k: cast(MemberBasis, text(v)) for k, v in load_map(r[26]).items()}
    return Override(
        text(r[0]),
        integer(r[1]),
        text(r[2]),
        optional_text(r[3]),
        cast(Audience, text(r[4])),
        optional_text(r[5]),
        integer(r[6]),
        text(r[7]),
        cast(StartedBy, text(r[8])),
        cast(SourceKind, text(r[9])),
        text(r[10]),
        cadence(json.loads(text(r[11]))),
        text(r[12]),
        text(r[13]),
        cast(Literal["active", "ended"], text(r[14])),
        timestamp(r[15]),
        cast(EndsKind, text(r[16])),
        optional_time(r[17]),
        text(r[18]),
        optional_text(r[19]),
        optional_time(r[20]),
        cast(EndReason | None, optional_text(r[21])),
        text(r[22]),
        load_strings(r[23]),
        text(r[24]),
        load_strings(r[25]),
        basis,
        cast(OriginMode, text(r[27])),
        timestamp(r[28]),
        load_strings(r[29]),
        effect(r[30]),
        boolean(r[31]),
        optional_text(r[32]),
    )


def instance_values(i: QuestionInstance) -> tuple[SqlValue, ...]:
    return (
        i.instance_id,
        i.override_id,
        i.person_id,
        i.question_set_id,
        i.slot,
        dump(i.question_ids),
        instant(i.due_from),
        instant(i.due_until),
        dump(dict(i.answers)),
        i.state,
        i.revision,
    )


def read_instance(r: tuple[SqlValue, ...]) -> QuestionInstance:
    answers = {k: text(v) for k, v in load_map(r[8]).items()}
    return QuestionInstance(
        text(r[0]),
        text(r[1]),
        text(r[2]),
        text(r[3]),
        text(r[4]),
        load_strings(r[5]),
        timestamp(r[6]),
        timestamp(r[7]),
        answers,
        cast(Literal["open", "answered", "partially_answered", "unanswered"], text(r[9])),
        integer(r[10]),
    )


def activation_values(a: CadenceActivation) -> tuple[SqlValue, ...]:
    return (
        a.activation_id,
        a.revision,
        a.binding_id,
        a.item_id,
        a.person_id,
        dump(asdict(a.cadence)),
        instant(a.started_at),
        a.ends_kind,
        instant(a.ends_at),
        a.state,
        instant(a.ended_at),
        a.end_reason,
        a.source_kind,
        a.source_ref,
        instant(a.source_at),
        a.source_origin_mode,
        a.config_release_id,
    )


def read_activation(r: tuple[SqlValue, ...]) -> CadenceActivation:
    return CadenceActivation(
        text(r[0]),
        integer(r[1]),
        text(r[2]),
        text(r[3]),
        text(r[4]),
        cadence(json.loads(text(r[5]))),
        timestamp(r[6]),
        cast(Literal["days", "mission_event", "warning_cleared"], text(r[7])),
        optional_time(r[8]),
        cast(Literal["active", "ended"], text(r[9])),
        optional_time(r[10]),
        cast(EndReason | None, optional_text(r[11])),
        cast(SourceKind, text(r[12])),
        text(r[13]),
        timestamp(r[14]),
        cast(OriginMode, text(r[15])),
        text(r[16]),
    )
