"""Loader checks of CONTRACTS section 11 that read several documents of one candidate release: 3 to 8, 10, 11, the
three parts of 12, 14, 15 and the two parts of 16. Each check is pure: it parses the candidate's raw bytes, never
reads a file or a clock, and returns kernel findings. A document a check needs that is missing or shaped so the
check cannot evaluate it is a finding of that check (fail closed); check 1 reports the schema detail."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from crew_health.config.domain.checks import parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.text_rules import AmountRules
from crew_health.kernel.config import CandidateRelease, ConfigName, Finding, ReleaseCheck

type Json = dict[str, object]

EH = ConfigName.ENVIRONMENT_HAZARDS
EP = ConfigName.ENVIRONMENT_PROFILES
HP = ConfigName.HELP_PRESENTATION
KM = ConfigName.KIT_MAP
OP = ConfigName.ONBOARD_PLANS
QU = ConfigName.QUESTIONNAIRES
RF = ConfigName.RESPONDER_FLOWS
RR = ConfigName.ROUTINE_RULES
RS = ConfigName.ROUTINE_SCHEDULE
RO = ConfigName.ROLE_RULES
SC = ConfigName.SENSOR_CATALOG
SK = ConfigName.SKILLS_CATALOG
SL = ConfigName.SENSOR_LIMITS
WR = ConfigName.WARNING_RULES

UNBOUND_TRIGGER = "6.unbound_trigger"  # ConfigLoader lists these findings in validation.warnings (check 6)

# Safety vocabulary of checks 3 and 14, held in code: configuration may not weaken a safety check (AGENTS rule 1).
ONBOARD_WORDS = "as in the onboard procedure"
_MEDICINE = re.compile(r"\b(?:medicines?|inhalers?|adrenaline|glucose|glucagon|pain relief)\b", re.IGNORECASE)
_AMOUNTS = AmountRules(max_text_bytes=8388608, max_matches=10000)  # the loader's document byte bound
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\d)")
_RTDC_TABLE = "removal to definitive care"
# Fixed vocabulary (CONTRACTS section 11, common.schema.json): the environments, the plan stages in their order.
# environment-hazards.json also lists the key eva, which is an overlay and never an environment of its own.
ENVIRONMENTS = ("station_leo", "transit", "lunar_surface", "mars_surface")
STAGES = ("check_another_way", "do_now", "watch", "if_worse", "medical_officer", "return", "ground_log")
_ABOARD_STAGES = frozenset({"check_another_way", "do_now", "watch"})
_WATCH_STAGES = frozenset({"check_another_way", "watch"})
_EXCLUSIONS = ("never_subject", "not_on_eva_for_on_site")
_CASCADE = (("officer_rank", 1), ("officer_rank", 2), ("freshest_skill_holder", None))


class _Shape(Exception):
    """The release lacks a document, or a value this check needs has another shape; check 1 says why."""

    def __init__(self, document: ConfigName | None, message: str) -> None:
        super().__init__(message)
        self.document = document
        self.message = message


class _Documents:
    """The candidate's documents parsed once per check run; at most one entry per ConfigName."""

    def __init__(self, candidate: CandidateRelease) -> None:
        self._candidate = candidate
        self._parsed: dict[ConfigName, Json] = {}

    def __call__(self, name: ConfigName) -> Json:
        cached = self._parsed.get(name)
        if cached is not None:
            return cached
        view = self._candidate.documents.get(name)
        if view is None:
            raise _Shape(name, "document missing from the release")
        try:
            value = parse_document(view.raw)
        except ConfigInvalid:
            raise _Shape(name, "document is not valid JSON") from None
        if not isinstance(value, dict):
            raise _Shape(name, "document is not an object")
        self._parsed[name] = cast(Json, value)
        return self._parsed[name]

    def present(self) -> tuple[ConfigName, ...]:
        return tuple(sorted(self._candidate.documents))


type Body = Callable[[_Documents], list[Finding]]


@dataclass(frozen=True, slots=True)
class PackageCheck:
    """One required loader contributor (ReleaseCheck). An evaluation it cannot finish is a finding, never a skip."""

    check_id: str
    body: Body

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        try:
            return tuple(self.body(_Documents(candidate)))
        except _Shape as error:
            return (Finding(self.check_id, error.document, "/", error.message),)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            return (Finding(self.check_id, None, "/", "check could not evaluate: " + type(error).__name__),)


def _obj(value: object) -> Json:
    if not isinstance(value, dict):
        raise _Shape(None, "expected an object")
    return cast(Json, value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _Shape(None, "expected an array")
    return cast(list[object], value)


def _objs(value: object) -> list[Json]:
    return [_obj(item) for item in _list(value)]


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise _Shape(None, "expected a string")
    return value


def _strs(value: object) -> list[str]:
    return [_str(item) for item in _list(value)]


def _ids(items: list[Json], key: str) -> frozenset[str]:
    return frozenset(_str(item.get(key)) for item in items)


def _index(value: object, size: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size:
        return value
    return None


def _number(value: object) -> int | float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return None


def _same(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    return left == right


def _names_amount(text: str) -> bool:
    try:
        return bool(_AMOUNTS.find(text))
    except ConfigInvalid:
        return True  # text beyond the bound cannot be shown free of amounts


def _names_medicine(text: str) -> bool:
    return _MEDICINE.search(text) is not None


def _says_onboard(text: str) -> bool:
    return ONBOARD_WORDS in text.casefold()


def _normal(text: str) -> str:
    return " ".join(text.split())


def _flow_ids(docs: _Documents) -> frozenset[str]:
    return _ids(_objs(docs(RF).get("flows")), "id")


def _accepted(items: list[Json]) -> list[Json]:
    """Rejected content is never served, so for references it does not exist (E28 3.1)."""
    return [item for item in items if item.get("review_state") != "rejected"]


# Check 3: responder flows.


def _responder_flows(docs: _Documents) -> list[Finding]:
    document = docs(RF)
    triage = _objs(document.get("triage"))
    flows = _objs(document.get("flows"))
    targets = _ids(triage, "id") | _ids(flows, "id")
    found = []
    for t, node in enumerate(triage):
        for a, answer in enumerate(_objs(node.get("answers"))):
            if _str(answer.get("goes_to")) not in targets:
                pointer = f"/triage/{t}/answers/{a}/goes_to"
                found.append(Finding("3", RF, pointer, "triage answer goes to no triage node or flow"))
    for f, flow in enumerate(flows):
        found.extend(_flow_text(flow, f"/flows/{f}"))
    return found


def _flow_text(flow: Json, pointer: str) -> list[Finding]:
    found = [
        Finding("3", RF, f"{pointer}/immediate_steps/{s}", "step text names an amount")
        for s, text in enumerate(_strs(flow.get("immediate_steps")))
        if _names_amount(text)
    ]
    for s, step in enumerate(_objs(flow.get("treatment_steps"))):
        here = f"{pointer}/treatment_steps/{s}"
        texts = {"step": _str(step.get("step")), "kit": _str(step.get("kit"))}
        found.extend(
            Finding("3", RF, f"{here}/{key}", "step or kit text names an amount")
            for key, text in texts.items()
            if _names_amount(text)
        )
        named = any(_names_medicine(text) for text in texts.values())
        if named and not any(_says_onboard(text) for text in texts.values()):
            found.append(Finding("3", RF, here, f'a step that names a medicine says "{ONBOARD_WORDS}"'))
    return found


# Check 4: sensor limits against environment-hazards.json.


def _hazard_levels(docs: _Documents) -> dict[str, list[Json]]:
    return {_str(h.get("id")): _objs(h.get("levels")) for h in _objs(docs(EH).get("hazards"))}


def _sensor_limits(docs: _Documents) -> list[Finding]:
    levels = _hazard_levels(docs)
    found: list[Finding] = []
    marks: set[tuple[str, str]] = set()
    for i, limit in enumerate(_objs(docs(SL).get("limits"))):
        found.extend(_limit_copy(limit, f"/limits/{i}", levels))
        if limit.get("card_mark") is not True:
            continue
        sensor = _str(limit.get("sensor_type_id"))
        for environment in _strs(limit.get("environments")):
            if (sensor, environment) in marks:
                message = f"a second card mark for {sensor} in {environment}"
                found.append(Finding("4", SL, f"/limits/{i}/card_mark", message))
            marks.add((sensor, environment))
    return found


def _limit_copy(limit: Json, pointer: str, levels: dict[str, list[Json]]) -> list[Finding]:
    copy = _obj(limit.get("hazard_level"))
    hazard = levels.get(_str(copy.get("hazard_id")), [])
    index = _index(copy.get("index"), len(hazard))
    if index is None:
        return [
            Finding("4", SL, pointer + "/hazard_level", "hazard level is not in environment-hazards.json")
        ]
    level = hazard[index]
    found = [
        Finding("4", SL, f"{pointer}/hazard_level/{key}", f"{key} differs from environment-hazards.json")
        for key in ("level", "value", "unit", "window")
        if not _same(copy.get(key), level.get(key))
    ]
    source = limit.get("value_from")
    if not _value_follows(source, limit.get("value"), level):
        found.append(Finding("4", SL, pointer + "/value", f"value does not follow value_from {source}"))
    return found


def _value_follows(source: object, value: object, level: Json) -> bool:
    if source == "pending":
        return value is None
    number = _number(value)
    if number is None:
        return False
    if source == "level_value":
        return _same(number, level.get("value"))
    if source == "rule_text":
        return any(float(match) == number for match in _NUMBER.findall(_str(level.get("rule"))))
    return False


# Check 5: warning rules.


def _warning_rules(docs: _Documents) -> list[Finding]:
    definitions = _objs(docs(WR).get("definitions"))
    named: dict[str, int] = {}
    for definition in definitions:
        condition = _obj(definition.get("condition"))
        if condition.get("kind") == "sensor_limit":
            limit_id = _str(condition.get("limit_id"))
            named[limit_id] = named.get(limit_id, 0) + 1
    found = []
    for i, limit in enumerate(_objs(docs(SL).get("limits"))):
        limit_id = _str(limit.get("limit_id"))
        count = named.get(limit_id, 0)
        if limit.get("role") == "warning" and limit.get("status") == "active" and count != 1:
            message = f"active warning limit {limit_id} has {count} definitions, needs exactly one"
            found.append(Finding("5", SL, f"/limits/{i}", message))
    found.extend(
        Finding("5", WR, f"/definitions/{j}/level", "an assistant definition is info or watch")
        for j, definition in enumerate(definitions)
        if definition.get("raised_by") == "assistant" and definition.get("level") not in ("info", "watch")
    )
    return found


# Check 6: routine rules bindings against routine-schedule.json.


def _routine_rules(docs: _Documents) -> list[Finding]:
    items = _objs(docs(RS).get("items"))
    by_id = {_str(item.get("id")): item for item in items}
    bound: set[tuple[str, int]] = set()
    found = []
    for b, binding in enumerate(_objs(docs(RR).get("bindings"))):
        binds = _obj(binding.get("binds"))
        pointer = f"/bindings/{b}/binds"
        item_id = _str(binds.get("item_id"))
        item = by_id.get(item_id)
        if item is None:
            found.append(Finding("6", RR, pointer + "/item_id", "routine item does not exist"))
        elif binds.get("kind") == "trigger":
            found.extend(_trigger_binding(binds, item, pointer, bound))
        elif binds.get("kind") == "cadence_event":
            found.extend(_cadence_binding(binds, item, pointer))
        else:
            found.append(Finding("6", RR, pointer + "/kind", "unknown binding kind"))
    for i, item in enumerate(items):
        found.extend(
            Finding(UNBOUND_TRIGGER, RS, f"/items/{i}/raise_frequency_when/{t}", "no binding: stays manual")
            for t in range(len(_list(item.get("raise_frequency_when"))))
            if (_str(item.get("id")), t) not in bound
        )
    return found


def _trigger_binding(binds: Json, item: Json, pointer: str, bound: set[tuple[str, int]]) -> list[Finding]:
    triggers = _objs(item.get("raise_frequency_when"))
    index = _index(binds.get("trigger_index"), len(triggers))
    if index is None:
        return [Finding("6", RR, pointer + "/trigger_index", "trigger index does not exist")]
    bound.add((_str(item.get("id")), index))
    if _str(binds.get("trigger_text")) != _str(triggers[index].get("trigger")):
        return [
            Finding("6", RR, pointer + "/trigger_text", "trigger text differs from routine-schedule.json")
        ]
    return []


def _cadence_binding(binds: Json, item: Json, pointer: str) -> list[Finding]:
    cadence = _obj(item.get("cadence"))
    environment = binds.get("environment_id")
    if environment is None:
        selected = _obj(cadence.get("default"))
    else:
        selected = _obj(_obj(cadence.get("by_environment")).get(_str(environment)))
    words = {selected.get("event"), selected.get("phase")} - {None}
    if _str(binds.get("event_text")) not in words:
        return [Finding("6", RR, pointer + "/event_text", "event text differs from the item's cadence words")]
    return []


# Check 7: environment profiles.


def _environment_profiles(docs: _Documents) -> list[Finding]:
    environments = ENVIRONMENTS
    sensors = _ids(_objs(docs(SC).get("sensor_types")), "id")
    counts: dict[str, int] = {}
    found = []
    for p, profile in enumerate(_objs(docs(EP).get("profiles"))):
        environment = _str(profile.get("environment_id"))
        counts[environment] = counts.get(environment, 0) + 1
        if environment not in environments:
            found.append(
                Finding("7", EP, f"/profiles/{p}/environment_id", "profile for an unknown environment")
            )
        found.extend(
            Finding(
                "7", EP, f"/profiles/{p}/sensor_type_ids/{s}", f"sensor type {sensor} is not in the catalog"
            )
            for s, sensor in enumerate(_strs(profile.get("sensor_type_ids")))
            if sensor not in sensors
        )
    found.extend(
        Finding("7", EP, "/profiles", f"{environment} has {counts.get(environment, 0)} profiles, needs one")
        for environment in environments
        if counts.get(environment, 0) != 1
    )
    return found


# Check 8: questionnaires.


def _question_references(docs: _Documents) -> list[tuple[ConfigName, str, str]]:
    questionnaires = docs(QU)
    references = [
        (QU, f"/{group}/{key}/{i}", question)
        for group, key in (
            ("daily_check", "question_ids"),
            ("daily_check", "required_question_ids"),
            ("red_flags", "question_ids"),
        )
        for i, question in enumerate(_strs(_obj(questionnaires.get(group)).get(key)))
    ]
    for s, question_set in enumerate(_objs(questionnaires.get("question_sets"))):
        references.extend(
            (QU, f"/question_sets/{s}/question_ids/{i}", question)
            for i, question in enumerate(_strs(question_set.get("question_ids")))
        )
    references.extend(
        (HP, f"/red_flag_flows/{i}/question_id", _str(entry.get("question_id")))
        for i, entry in enumerate(_objs(docs(HP).get("red_flag_flows")))
    )
    misses = _obj(docs(RR).get("misses"))
    references.append((RR, "/misses/question_id", _str(misses.get("question_id"))))
    for d, definition in enumerate(_objs(docs(WR).get("definitions"))):
        condition = _obj(definition.get("condition"))
        if condition.get("kind") == "answer":
            references.append(
                (WR, f"/definitions/{d}/condition/question_id", _str(condition.get("question_id")))
            )
    return references


def _questionnaires(docs: _Documents) -> list[Finding]:
    questionnaires = docs(QU)
    questions = _objs(questionnaires.get("questions"))
    known = _ids(questions, "id")
    sets = _ids(_objs(questionnaires.get("question_sets")), "id")
    flows = _flow_ids(docs)
    found = [
        Finding("8", document, pointer, f"question {question} does not exist")
        for document, pointer, question in _question_references(docs)
        if question not in known
    ]
    for b, binding in enumerate(_objs(docs(RR).get("bindings"))):
        question_set = _obj(binding.get("effect")).get("question_set_id")
        if question_set is not None and _str(question_set) not in sets:
            pointer = f"/bindings/{b}/effect/question_set_id"
            found.append(Finding("8", RR, pointer, f"question set {question_set} does not exist"))
    for q, question in enumerate(questions):
        for c, choice in enumerate(_objs(question.get("choices"))):
            flow = choice.get("flow_id")
            opens_flow = choice.get("opens") == "help_flow"
            if (opens_flow or flow is not None) and (flow is None or _str(flow) not in flows):
                pointer = f"/questions/{q}/choices/{c}/flow_id"
                found.append(Finding("8", QU, pointer, "the flow this choice opens does not exist"))
    return found


# Check 10: help presentation against responder-flows.json.


def _help_presentation(docs: _Documents) -> list[Finding]:
    presentation = docs(HP)
    triage = {_str(node.get("id")): len(_list(node.get("answers"))) for node in _objs(docs(RF).get("triage"))}
    flows = _flow_ids(docs)
    found = []
    if _str(presentation.get("start_node_id")) not in triage:
        found.append(Finding("10", HP, "/start_node_id", "start node is not a triage node"))
    for key in ("flow_levels", "red_flag_flows"):
        found.extend(
            Finding("10", HP, f"/{key}/{i}/flow_id", "flow does not exist in responder-flows.json")
            for i, entry in enumerate(_objs(presentation.get(key)))
            if _str(entry.get("flow_id")) not in flows
        )
    for i, entry in enumerate(_objs(presentation.get("answer_log"))):
        answers = triage.get(_str(entry.get("node_id")))
        if answers is None:
            found.append(Finding("10", HP, f"/answer_log/{i}/node_id", "triage node does not exist"))
        elif _index(entry.get("answer_index"), answers) is None:
            found.append(Finding("10", HP, f"/answer_log/{i}/answer_index", "triage answer does not exist"))
    return found


# Check 11: kit map references.


@dataclass(frozen=True, slots=True)
class _KitMapReferences:
    condition_ids: frozenset[str]

    def __call__(self, docs: _Documents) -> list[Finding]:
        kit_map = docs(KM)
        instruments = _objs(kit_map.get("instruments"))
        known = _ids(instruments, "id")
        found = _instrument_references(kit_map, known)
        if len(known) != len(instruments):
            found.append(Finding("11", KM, "/instruments", "instrument ids repeat"))
        found.extend(_capability_entries(kit_map, instruments))
        seen: set[str] = set()
        for r, row in enumerate(_objs(kit_map.get("conditions"))):
            condition = _str(row.get("condition_id"))
            if condition not in self.condition_ids or condition in seen:
                message = f"{condition} is not a condition-assessments-119.csv row or appears twice"
                found.append(Finding("11", KM, f"/conditions/{r}/condition_id", message))
            seen.add(condition)
        return found


def _instrument_references(kit_map: Json, known: frozenset[str]) -> list[Finding]:
    named: list[tuple[str, str]] = []
    kits = _obj(kit_map.get("kits"))
    for environment in sorted(kits):
        named.extend((f"/kits/{environment}/{i}", item) for i, item in enumerate(_strs(kits[environment])))
    named.extend((f"/candidates/{i}", item) for i, item in enumerate(_strs(kit_map.get("candidates"))))
    for r, row in enumerate(_objs(kit_map.get("conditions"))):
        for t, test in enumerate(_objs(row.get("confirming_tests"))):
            pointer = f"/conditions/{r}/confirming_tests/{t}/instruments"
            named.extend((f"{pointer}/{i}", item) for i, item in enumerate(_strs(test.get("instruments"))))
    return [
        Finding("11", KM, pointer, f"instrument {item} is not in instruments")
        for pointer, item in named
        if item not in known
    ]


def _capability_entries(kit_map: Json, instruments: list[Json]) -> list[Finding]:
    provided = {code for instrument in instruments for code in _strs(instrument.get("capability_codes"))}
    codes = [_str(entry.get("code")) for entry in _objs(kit_map.get("capabilities"))]
    found = [
        Finding("11", KM, "/capabilities", f"capability {code} has {codes.count(code)} entries, needs one")
        for code in sorted(provided)
        if codes.count(code) != 1
    ]
    found.extend(
        Finding("11", KM, f"/capabilities/{c}/code", f"no instrument provides {code}")
        for c, code in enumerate(codes)
        if code not in provided
    )
    return found


# Check 12: sensor limits against the catalog, effect filters against hazards, reviewed kit map rows.


def _limit_catalog(docs: _Documents) -> list[Finding]:
    types = {_str(item.get("id")): item for item in _objs(docs(SC).get("sensor_types"))}
    found = []
    for i, limit in enumerate(_objs(docs(SL).get("limits"))):
        sensor = types.get(_str(limit.get("sensor_type_id")))
        if sensor is None:
            found.append(
                Finding("12.sensor", SL, f"/limits/{i}/sensor_type_id", "sensor type is not in the catalog")
            )
            continue
        if _str(limit.get("aggregation_id")) not in _ids(_objs(sensor.get("aggregations")), "id"):
            found.append(
                Finding("12.sensor", SL, f"/limits/{i}/aggregation_id", "aggregation is not on the type")
            )
        if _str(limit.get("unit")) != _str(sensor.get("unit")):
            found.append(Finding("12.sensor", SL, f"/limits/{i}/unit", "unit is not the catalog unit"))
    return found


def _effect_filters(docs: _Documents) -> list[Finding]:
    effects = {
        _str(hazard.get("id")): [_str(effect.get("effect")) for effect in _objs(hazard.get("effects"))]
        for hazard in _objs(docs(EH).get("hazards"))
    }
    found = []
    for i, item in enumerate(_objs(docs(EP).get("effect_filters"))):
        listed = effects.get(_str(item.get("hazard_id")), [])
        index = _index(item.get("effect_index"), len(listed))
        if index is None:
            found.append(
                Finding("12.effect", EP, f"/effect_filters/{i}/effect_index", "hazard effect does not exist")
            )
        elif not listed[index].startswith(_str(item.get("effect_starts_with"))):
            pointer = f"/effect_filters/{i}/effect_starts_with"
            found.append(Finding("12.effect", EP, pointer, "does not match the start of the hazard effect"))
    return found


def _reviewed_rows(docs: _Documents) -> list[Finding]:
    return [
        Finding("12.kit", KM, f"/conditions/{r}/checked_by", "a reviewed row names checked_by")
        for r, row in enumerate(_objs(docs(KM).get("conditions")))
        if row.get("review_state") == "reviewed"
        and not (isinstance(row.get("checked_by"), str) and _str(row.get("checked_by")).strip())
    ]


# Check 14: onboard plans.


@dataclass(frozen=True, slots=True)
class _PlanContext:
    instruments: frozenset[str]
    consumable: Mapping[str, bool]
    skills: frozenset[str]
    landmarks: frozenset[str]
    flows: frozenset[str]
    returns: frozenset[str]


def _plan_context(docs: _Documents) -> _PlanContext:
    kit_map, catalog = docs(KM), docs(SK)
    supplies = _objs(kit_map.get("supplies"))
    returns = frozenset(
        _str(profile.get("environment_id"))
        for profile in _objs(docs(EP).get("profiles"))
        if profile.get("return_option") is not None
    )
    return _PlanContext(
        instruments=_ids(_objs(kit_map.get("instruments")), "id"),
        consumable={_str(item.get("supply_id")): item.get("consumable") is True for item in supplies},
        skills=_ids(_accepted(_objs(catalog.get("skills"))), "skill_id"),
        landmarks=_ids(_objs(catalog.get("landmarks")), "landmark_id"),
        flows=_flow_ids(docs),
        returns=returns,
    )


def _plan_steps(plan: Json, pointer: str) -> list[tuple[str, str, Json]]:
    """(stage_id, pointer, step) of every accepted step of an accepted plan, return-stage prepare steps included."""
    steps: list[tuple[str, str, Json]] = []
    for k, stage in enumerate(_objs(plan.get("stages"))):
        stage_id = _str(stage.get("stage_id"))
        key = "prepare" if stage_id == "return" else "steps"
        if stage_id == "ground_log":
            continue
        steps.extend(
            (stage_id, f"{pointer}/stages/{k}/{key}/{s}", step)
            for s, step in enumerate(_objs(stage.get(key)))
            if step.get("review_state") != "rejected"
        )
    return steps


def _stage_order(plan: Json, pointer: str, context: _PlanContext) -> list[Finding]:
    stages = _objs(plan.get("stages"))
    covered = frozenset(_strs(plan.get("environments")))
    found, previous = [], -1
    for k, stage in enumerate(stages):
        stage_id = _str(stage.get("stage_id"))
        rank = STAGES.index(stage_id) if stage_id in STAGES else -1
        if rank <= previous:
            found.append(
                Finding("14", OP, f"{pointer}/stages/{k}/stage_id", "stage out of order or repeated")
            )
        previous = max(previous, rank)
        if stage_id == "ground_log" and stage.get("blocking") is not False:
            found.append(Finding("14", OP, f"{pointer}/stages/{k}/blocking", "ground_log never blocks"))
        if stage_id == "return":
            found.extend(
                Finding("14", OP, f"{pointer}/stages/{k}/environments/{e}", f"no return here: {environment}")
                for e, environment in enumerate(_strs(stage.get("environments")))
                if environment not in covered or environment not in context.returns
            )
    if not stages or stages[-1].get("stage_id") != "ground_log":
        found.append(Finding("14", OP, f"{pointer}/stages", "ground_log is required and last"))
    return found


def _step_names(step: Json) -> list[tuple[str, str, str]]:
    """(pointer suffix, kind, id) of every instrument, supply, skill, landmark and flow a step names."""
    needs = _obj(step.get("needs"))
    names = [
        ("/needs/instruments/" + str(i), "instrument", item)
        for i, item in enumerate(_strs(needs.get("instruments")))
    ]
    names.extend(
        (f"/needs/supplies/{i}/supply_id", "supply", _str(item.get("supply_id")))
        for i, item in enumerate(_objs(needs.get("supplies")))
    )
    names.extend(
        ("/needs/skills/" + str(i), "skill", item) for i, item in enumerate(_strs(needs.get("skills")))
    )
    who = _str(step.get("who"))
    if who.startswith("skill:"):
        names.append(("/who", "skill", who.removeprefix("skill:")))
    for key, kind in (
        ("guided_check_skill_id", "skill"),
        ("landmark_id", "landmark"),
        ("opens_flow_id", "flow"),
    ):
        if step.get(key) is not None:
            names.append(("/" + key, kind, _str(step.get(key))))
    return names


def _step_references(
    step: Json, here: str, context: _PlanContext, steps: Mapping[str, Json]
) -> list[Finding]:
    known = {
        "instrument": context.instruments,
        "supply": frozenset(context.consumable),
        "skill": context.skills,
        "landmark": context.landmarks,
        "flow": context.flows,
    }
    found = [
        Finding("14", OP, here + suffix, f"{kind} {item} does not exist")
        for suffix, kind, item in _step_names(step)
        if item not in known[kind]
    ]
    substitute = step.get("substitute_step_id")
    if substitute is not None and steps.get(_str(substitute), {}).get("substitute_only") is not True:
        message = "substitute is not a substitute_only step of this plan"
        found.append(Finding("14", OP, here + "/substitute_step_id", message))
    return found


def _step_stage(stage_id: str, step: Json, here: str, context: _PlanContext) -> list[Finding]:
    found = []
    who = _str(step.get("who"))
    if who == "crew_medical_officer" and stage_id not in ("medical_officer", "return"):
        found.append(
            Finding("14", OP, here + "/who", "medical officer steps only in medical_officer or return")
        )
    guided = step.get("guided_check_skill_id")
    if guided is not None and (stage_id != "check_another_way" or who != f"skill:{_str(guided)}"):
        message = "a guided check is only in check_another_way, by a holder of its skill"
        found.append(Finding("14", OP, here + "/guided_check_skill_id", message))
    opens = step.get("opens_help") is True or step.get("opens_flow_id") is not None
    if opens and stage_id != "if_worse":
        found.append(Finding("14", OP, here + "/opens_help", "only if_worse steps open help"))
    for i, supply in enumerate(_objs(_obj(step.get("needs")).get("supplies"))):
        consumable = context.consumable.get(_str(supply.get("supply_id")), True)
        if not consumable and supply.get("per_use") is not None:
            found.append(
                Finding("14", OP, f"{here}/needs/supplies/{i}/per_use", "non-consumable per_use is null")
            )
    return found


def _step_text(step: Json, here: str) -> list[Finding]:
    medicine = step.get("medicine")
    found = []
    if medicine is not None and medicine != {"named_as": ONBOARD_WORDS}:
        found.append(Finding("14", OP, here + "/medicine", f'medicine holds named_as "{ONBOARD_WORDS}" only'))
    for key in ("text", "text_first_time", "cannot_replace_text"):
        value = step.get(key)
        if value is None:
            continue
        text = _str(value)
        if _names_amount(text):
            found.append(Finding("14", OP, f"{here}/{key}", "step text names an amount"))
        if key != "cannot_replace_text" and _names_medicine(text) and not _says_onboard(text):
            found.append(
                Finding("14", OP, f"{here}/{key}", f'a step naming a medicine says "{ONBOARD_WORDS}"')
            )
    return found


def _stage_texts(plan: Json, pointer: str) -> list[Finding]:
    texts: list[tuple[str, str]] = []
    for k, stage in enumerate(_objs(plan.get("stages"))):
        for key in ("criteria", "items"):
            texts.extend(
                (f"{pointer}/stages/{k}/{key}/{i}/text", _str(entry.get("text")))
                for i, entry in enumerate(_objs(stage.get(key, [])))
            )
    return [Finding("14", OP, here, "text names an amount") for here, text in texts if _names_amount(text)]


def _teach_texts(docs: _Documents) -> list[Finding]:
    found: list[Finding] = []
    for s, skill in enumerate(_objs(docs(SK).get("skills"))):
        for t, step in enumerate(_objs(skill.get("teach_steps"))):
            found.extend(
                Finding("14", SK, f"/skills/{s}/teach_steps/{t}/{key}", "teach text names an amount")
                for key in ("text", "text_first_time")
                if step.get(key) is not None and _names_amount(_str(step.get(key)))
            )
    return found


@dataclass(frozen=True, slots=True)
class _OnboardPlans:
    form_pages: Mapping[str, tuple[str, ...]]

    def __call__(self, docs: _Documents) -> list[Finding]:
        context = _plan_context(docs)
        found = _teach_texts(docs)
        for p, plan in enumerate(_objs(docs(OP).get("plans"))):
            if plan.get("review_state") == "rejected":
                continue
            pointer = f"/plans/{p}"
            steps = _plan_steps(plan, pointer)
            by_id = {_str(step.get("step_id")): step for _, _, step in steps}
            found.extend(_stage_order(plan, pointer, context))
            found.extend(_stage_texts(plan, pointer))
            for stage_id, here, step in steps:
                found.extend(_step_references(step, here, context, by_id))
                found.extend(_step_stage(stage_id, step, here, context))
                found.extend(_step_text(step, here))
        found.extend(_form_quotes(docs, _SavedForms(self.form_pages, _source_urls(docs))))
        return found


# Check 14, form quotes: found on the cited page of the saved form text; rtdc from a form with an RTDC table.


@dataclass(frozen=True, slots=True)
class _SavedForms:
    pages: Mapping[str, tuple[str, ...]]
    urls: Mapping[str, str]

    def page(self, source_id: str, page: object) -> str | None:
        form = self.pages.get(self.urls.get(source_id, ""))
        index = _index(page, len(form) + 1 if form is not None else 0)
        if form is None or index is None or index < 1:
            return None
        return _normal(form[index - 1])


def _source_urls(docs: _Documents) -> dict[str, str]:
    """Source ID to URL from every source list the release itself carries (research files and contributors)."""
    urls: dict[str, str] = {}
    for name in docs.present():
        for entry in _objs(docs(name).get("sources", [])):
            source = entry.get("source_id", entry.get("id"))
            if isinstance(source, str) and isinstance(entry.get("url"), str):
                urls.setdefault(source, _str(entry.get("url")))
    return urls


def _quote_finding(forms: _SavedForms, document: ConfigName, pointer: str, quote: Json) -> list[Finding]:
    page = forms.page(_str(quote.get("source_id")), quote.get("page"))
    if page is None:
        return [Finding("14", document, pointer, "form quote cites no page of a saved form")]
    if _normal(_str(quote.get("quote"))) not in page:
        return [
            Finding("14", document, pointer + "/quote", "quote is not on the cited page of the saved form")
        ]
    return []


def _rtdc_finding(forms: _SavedForms, document: ConfigName, pointer: str, rtdc: Json) -> list[Finding]:
    page = forms.page(_str(rtdc.get("source_id")), rtdc.get("page"))
    if page is None or _RTDC_TABLE not in page.casefold():
        return [Finding("14", document, pointer, "rtdc does not come from a saved form's RTDC table")]
    return []


def _form_quotes(docs: _Documents, forms: _SavedForms) -> list[Finding]:
    found: list[Finding] = []
    for r, row in enumerate(_objs(docs(KM).get("conditions"))):
        for key in ("ground_needs", "treatment_scope"):
            if row.get(key) is not None:
                found.extend(_quote_finding(forms, KM, f"/conditions/{r}/{key}", _obj(row.get(key))))
        if row.get("rtdc") is not None:
            found.extend(_rtdc_finding(forms, KM, f"/conditions/{r}/rtdc", _obj(row.get("rtdc"))))
    for p, plan in enumerate(_objs(docs(OP).get("plans"))):
        if plan.get("review_state") == "rejected":
            continue
        for _, here, step in _plan_steps(plan, f"/plans/{p}"):
            task = step.get("replaces_ground_task")
            if task is not None:
                found.extend(_quote_finding(forms, OP, here + "/replaces_ground_task", _obj(task)))
        for k, stage in enumerate(_objs(plan.get("stages"))):
            if stage.get("stage_id") == "return" and stage.get("rtdc") is not None:
                found.extend(_rtdc_finding(forms, OP, f"/plans/{p}/stages/{k}/rtdc", _obj(stage.get("rtdc"))))
    return found


# Check 15: a plan aboard for every kit map row in every environment (EI3).


@dataclass(frozen=True, slots=True)
class _Stock:
    kit: frozenset[str]
    packed: Mapping[str, object]
    consumable: Mapping[str, bool]

    def aboard(self, step: Json) -> bool:
        needs = _obj(step.get("needs"))
        if not frozenset(_strs(needs.get("instruments"))) <= self.kit:
            return False
        return all(self._supply_aboard(_obj(supply)) for supply in _list(needs.get("supplies")))

    def _supply_aboard(self, supply: Json) -> bool:
        supply_id = _str(supply.get("supply_id"))
        if supply_id not in self.packed:
            return False
        quantity, per_use = _number(self.packed[supply_id]), _number(supply.get("per_use"))
        if quantity == 0:
            return False
        short = quantity is not None and per_use is not None and quantity < per_use
        return not (self.consumable.get(supply_id, True) and short)


def _stocks(kit_map: Json, environments: tuple[str, ...]) -> dict[str, _Stock]:
    kits, stock = _obj(kit_map.get("kits")), _obj(kit_map.get("stock"))
    consumable = {
        _str(item.get("supply_id")): item.get("consumable") is True for item in _objs(kit_map.get("supplies"))
    }
    return {
        environment: _Stock(
            frozenset(_strs(kits.get(environment, []))),
            {_str(row.get("supply_id")): row.get("quantity") for row in _objs(stock.get(environment, []))},
            consumable,
        )
        for environment in environments
    }


def _plan_aboard(plan: Json, environment: str, stock: _Stock) -> bool:
    if environment not in _strs(plan.get("environments")):
        return False
    here = [
        (stage_id, step)
        for stage_id, _, step in _plan_steps(plan, "")
        if step.get("environments") is None or environment in _strs(step.get("environments"))
    ]
    steps = {_str(step.get("step_id")): step for _, step in here}
    watched = any(
        stage_id in _WATCH_STAGES and step.get("substitute_only") is not True for stage_id, step in here
    )
    return watched and any(
        stage_id in _ABOARD_STAGES and step.get("substitute_only") is not True and _runs(step, steps, stock)
        for stage_id, step in here
    )


def _any_crew(step: Json) -> bool:
    return step.get("who") == "any_crew" and not _list(_obj(step.get("needs")).get("skills"))


def _runs(step: Json, steps: Mapping[str, Json], stock: _Stock) -> bool:
    if not _any_crew(step):
        return False
    if stock.aboard(step):
        return True
    substitute = steps.get(_str(step.get("substitute_step_id") or ""))
    return substitute is not None and _any_crew(substitute) and stock.aboard(substitute)


def _plans_aboard(docs: _Documents) -> list[Finding]:
    environments = ENVIRONMENTS
    kit_map = docs(KM)
    stocks = _stocks(kit_map, environments)
    plans = {_str(plan.get("plan_id")): plan for plan in _accepted(_objs(docs(OP).get("plans")))}
    found: list[Finding] = []
    for r, row in enumerate(_objs(kit_map.get("conditions"))):
        if row.get("review_state") == "rejected":
            continue
        condition = _str(row.get("condition_id"))
        plan = plans.get(_str(row.get("plan_id") or ""))
        written = plan is not None and _obj(plan.get("scope")).get("condition_id") == condition
        found.extend(
            Finding("15", KM, f"/conditions/{r}", f"{condition} has no plan aboard in {environment}")
            for environment in environments
            if plan is None or not written or not _plan_aboard(plan, environment, stocks[environment])
        )
    return found


# Check 16: skills catalog and role rules.


def _skills_catalog(docs: _Documents) -> list[Finding]:
    catalog, flows = docs(SK), _objs(docs(RF).get("flows"))
    immediate = {_str(flow.get("id")): len(_list(flow.get("immediate_steps"))) for flow in flows}
    regions = {
        _str(item.get("landmark_id")): item.get("body_region") for item in _objs(catalog.get("landmarks"))
    }
    capabilities = _ids(_objs(docs(KM).get("capabilities")), "code")
    supplies = _ids(_objs(docs(KM).get("supplies")), "supply_id")
    found: list[Finding] = []
    for s, skill in enumerate(_objs(catalog.get("skills"))):
        pointer = f"/skills/{s}"
        for t, step in enumerate(_objs(skill.get("teach_steps"))):
            found.extend(_teach_step(step, t, f"{pointer}/teach_steps/{t}", immediate, regions))
        for c, check in enumerate(_objs(skill.get("checks"))):
            found.extend(_skill_check(check, f"{pointer}/checks/{c}", capabilities))
        for d, drill in enumerate(_objs(skill.get("drill_scenarios"))):
            found.extend(
                Finding(
                    "16.skills", SK, f"{pointer}/drill_scenarios/{d}/needs_supplies/{i}", "supply missing"
                )
                for i, supply in enumerate(_strs(drill.get("needs_supplies")))
                if supply not in supplies
            )
    known = _ids(_accepted(_objs(catalog.get("skills"))), "skill_id")
    for i, item in enumerate(_objs(docs(RS).get("items"))):
        found.extend(
            Finding("16.skills", RS, f"/items/{i}/skill_ids/{k}", f"skill {skill} does not exist")
            for k, skill in enumerate(_strs(item.get("skill_ids", [])))
            if skill not in known
        )
    return found


def _teach_step(
    step: Json, t: int, pointer: str, immediate: Mapping[str, int], regions: Mapping[str, object]
) -> list[Finding]:
    problems: list[tuple[str, str]] = []
    if step.get("step_no") != t + 1:
        problems.append(("/step_no", "teach steps are numbered from 1 in order"))
    origin = step.get("from_flow_step")
    if (step.get("text") is None) == (origin is None):
        problems.append(("", "exactly one of text or from_flow_step"))
    elif origin is not None:
        source = _obj(origin)
        size = immediate.get(_str(source.get("flow_id")), 0)
        if source.get("list") != "immediate_steps" or _index(source.get("index"), size) is None:
            problems.append(("/from_flow_step", "the responder flow step does not exist"))
    kind = step.get("kind")
    if (kind == "count") != (step.get("count_seconds") is not None):
        problems.append(("/count_seconds", "count steps and only they have count_seconds"))
    if (kind == "question") != bool(_list(step.get("choices"))):
        problems.append(("/choices", "question steps and only they have choices"))
    landmark = step.get("landmark_id")
    if landmark is not None and (
        _str(landmark) not in regions or regions[_str(landmark)] != step.get("body_region")
    ):
        problems.append(("/landmark_id", "landmark does not exist in the step's region"))
    return [Finding("16.skills", SK, pointer + suffix, message) for suffix, message in problems]


def _skill_check(check: Json, pointer: str, capabilities: frozenset[str]) -> list[Finding]:
    kind = check.get("kind")
    if kind == "count_agreement":
        reference = _obj(check.get("reference")).get("capability_code")
        found = []
        if check.get("tolerance") is None:
            found.append(
                Finding("16.skills", SK, pointer + "/tolerance", "a count agreement check has a tolerance")
            )
        if not isinstance(reference, str) or reference not in capabilities:
            message = "the reference capability does not exist"
            found.append(Finding("16.skills", SK, pointer + "/reference/capability_code", message))
        return found
    if kind == "compression_rate":
        target = check.get("target")
        band = _obj(target) if target is not None else {}
        low, high = _number(band.get("min")), _number(band.get("max"))
        if low is None or high is None or low > high or not _list(band.get("source_ids", [])):
            return [
                Finding("16.skills", SK, pointer + "/target", "a compression rate check has a sourced band")
            ]
    return []


def _role_rules(docs: _Documents) -> list[Finding]:
    rules = docs(RO)
    flows = _flow_ids(docs)
    skills = _ids(_accepted(_objs(docs(SK).get("skills"))), "skill_id")
    roles = _ids(_objs(rules.get("roles")), "role_id")
    needs: list[tuple[str, Json]] = []
    found = []
    for f, entry in enumerate(_objs(rules.get("flow_roles"))):
        if _str(entry.get("flow_id")) not in flows:
            found.append(Finding("16.roles", RO, f"/flow_roles/{f}/flow_id", "flow does not exist"))
        needs.extend((f"/flow_roles/{f}/roles/{i}", need) for i, need in enumerate(_objs(entry.get("roles"))))
    needs.extend((f"/default_roles/{i}", need) for i, need in enumerate(_objs(rules.get("default_roles"))))
    for pointer, need in needs:
        if _str(need.get("role_id")) not in roles:
            found.append(Finding("16.roles", RO, pointer + "/role_id", "role does not exist in roles"))
        skill = need.get("skill_id")
        if skill is not None and _str(skill) not in skills:
            found.append(Finding("16.roles", RO, pointer + "/skill_id", f"skill {skill} does not exist"))
    if _str(rules.get("plan_step_role")) not in roles:
        found.append(Finding("16.roles", RO, "/plan_step_role", "role does not exist in roles"))
    return found + _cascade_and_exclusions(rules)


def _cascade_and_exclusions(rules: Json) -> list[Finding]:
    cascade = tuple(
        (step.get("kind"), step.get("rank")) for step in _objs(rules.get("medical_officer_cascade"))
    )
    found = []
    if cascade != _CASCADE:
        message = "the cascade is first officer, second officer, then the freshest skill holder"
        found.append(Finding("16.roles", RO, "/medical_officer_cascade", message))
    kinds = {_str(rule.get("rule_id")): rule.get("kind") for rule in _objs(rules.get("eligibility"))}
    found.extend(
        Finding("16.roles", RO, "/eligibility", f"{rule} is an exclusion (EI9)")
        for rule in _EXCLUSIONS
        if kinds.get(rule) != "exclude"
    )
    return found


def saved_form_pages(raw: bytes) -> tuple[str, tuple[str, ...]]:
    """(url, page texts in page order) of one saved form, docs/product/cliff/forms/ICL<n>.json, for check 14."""
    form = parse_document(raw)
    if not isinstance(form, dict):
        raise ConfigInvalid("saved form is not an object")
    document = cast(Json, form)
    try:
        pages = _objs(document.get("pages"))
        if [page.get("page") for page in pages] != list(range(1, len(pages) + 1)):
            raise ConfigInvalid("saved form pages are not numbered from 1 in order")
        return _str(document.get("url")), tuple(_str(page.get("text")) for page in pages)
    except _Shape as error:
        raise ConfigInvalid("saved form shape invalid: " + error.message) from None


PACKAGE_CHECK_IDS: tuple[str, ...] = (
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "10",
    "11",
    "12.sensor",
    "12.effect",
    "12.kit",
    "14",
    "15",
    "16.skills",
    "16.roles",
)


def package_checks(
    *, condition_ids: frozenset[str], form_pages: Mapping[str, tuple[str, ...]]
) -> tuple[ReleaseCheck, ...]:
    """One check per required contributor id. `condition_ids` are the condition-assessments-119.csv rows (D22
    suffixes included); `form_pages` maps each saved form's URL to its page texts (see `saved_form_pages`)."""
    bodies: tuple[tuple[str, Body], ...] = (
        ("3", _responder_flows),
        ("4", _sensor_limits),
        ("5", _warning_rules),
        ("6", _routine_rules),
        ("7", _environment_profiles),
        ("8", _questionnaires),
        ("10", _help_presentation),
        ("11", _KitMapReferences(frozenset(condition_ids))),
        ("12.sensor", _limit_catalog),
        ("12.effect", _effect_filters),
        ("12.kit", _reviewed_rows),
        ("14", _OnboardPlans(dict(form_pages))),
        ("15", _plans_aboard),
        ("16.skills", _skills_catalog),
        ("16.roles", _role_rules),
    )
    assert tuple(check_id for check_id, _ in bodies) == PACKAGE_CHECK_IDS
    return tuple(PackageCheck(check_id, body) for check_id, body in bodies)
