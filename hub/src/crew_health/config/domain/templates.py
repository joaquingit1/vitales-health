from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from string import Formatter

from crew_health.config.domain.checks import parse_document, walk
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.kernel.config import CandidateRelease, Finding


def allowed_placeholders(field_key: str) -> frozenset[str]:
    document, *path = field_key.strip("/").split("/")
    field = path[0] if path else ""
    if document == "ui-copy":
        return _copy_placeholders(field)
    if (
        document == "environment-profiles"
        and "day_label" in path
        and path[-1] in {"template", "short_template", "inline_template"}
    ):
        return frozenset({"day", "total"})
    if document == "role-rules" and field == "reason_phrases":
        return frozenset("officer_name eva_word time skill_short day_label person_name".split())
    handlers = {
        "warning-rules": _warning_placeholders,
        "help-presentation": _help_placeholders,
        "skills-catalog": _skill_placeholders,
        "questionnaires": _question_placeholders,
    }
    handler = handlers.get(document)
    return handler(path) if handler is not None else frozenset()


def _copy_placeholders(field: str) -> frozenset[str]:
    rules = {
        "cadence_words": "n eva_word event phase",
        "occurrence_end_text": "day_label time overlay reason",
        "designed_states": "time in_time dataset sensor_name value unit_display source_label",
        "kit_check_templates": "instruments",
        "duration_words": "n",
        "plan_lines": "in_time origin_word step skill_short count total missing plan_label to_be_set_by time",
        "catch_up_words": "time step recheck topic",
        "guided_check_words": "n",
        "supply_words": "n name quantity",
        "readiness_words": "skill_short n",
        "context_words": "n met delay gap total count",
        "medicine_words": "time version lot n date p low high lower upper days threshold limit",
    }
    return frozenset(rules.get(field, "").split())


def _warning_placeholders(path: list[str]) -> frozenset[str]:
    if "content" in path and path[-1] in {"title", "what", "why", "citation_template", "fragment"}:
        return frozenset(
            "aggregation_label value unit unit_display limit_value location sensor_name source_label last_received_time answer_day_label answer_time day_short eva_word insight_title insight_text insight_why insight_fragment silence_text next_contact_time supply_name uses quantity per_use".split()
        )
    return frozenset()


def _help_placeholders(path: list[str]) -> frozenset[str]:
    if path and path[0] in {"answer_log", "broadcast", "log_templates", "ground_message"}:
        return frozenset(
            "subject_name opener_name helper_name helper_names place flow_title step note text disposition answers steps helping time delay in_time".split()
        )
    return frozenset()


def _skill_placeholders(path: list[str]) -> frozenset[str]:
    if path and (
        path[-1] == "card_title" or ("teach_steps" in path and path[-1] in {"text", "text_first_time"})
    ):
        return frozenset({"subject_name", "helper_name"})
    if "phrases" in path:
        return frozenset("count camera difference rate reason".split())
    return frozenset()


def _question_placeholders(path: list[str]) -> frozenset[str]:
    if path and path[0] == "question_sets" and path[-1] == "reason_template":
        return frozenset({"reason"})
    if path and path[0] == "questions" and path[-1] == "text":
        return frozenset({"value"})
    return frozenset()


def placeholders(template: str) -> tuple[str, ...]:
    try:
        pieces = tuple(Formatter().parse(template))
    except ValueError as exc:
        raise ConfigInvalid("invalid template syntax") from exc
    fields = []
    for _, name, specification, conversion in pieces:
        if name is not None:
            if not name.isidentifier() or specification or conversion:
                raise ConfigInvalid("template conversion or lookup forbidden")
            fields.append(name)
    return tuple(fields)


@dataclass(frozen=True, slots=True)
class TemplateCheck:
    check_id: str = "13"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        findings = []
        for name, document in sorted(candidate.documents.items()):
            for pointer, value in walk(parse_document(document.raw)):
                if not isinstance(value, str) or not ({"{", "}"} & set(value)):
                    continue
                try:
                    fields = placeholders(value)
                    if not set(fields) <= allowed_placeholders(name.value + pointer):
                        raise ConfigInvalid()
                except ConfigInvalid:
                    findings.append(
                        Finding(
                            self.check_id,
                            name,
                            pointer or "/",
                            "template has forbidden placeholder or syntax",
                        )
                    )
        return tuple(findings)


@dataclass(frozen=True, slots=True)
class PinnedTemplateRenderer:
    unknown: str
    on_missing: Callable[[str], None]

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        fields = placeholders(template)
        if not set(fields) <= allowed_placeholders(field_key):
            raise ConfigInvalid("template placeholder not allowed for field")
        supplied = {field: values.get(field, self.unknown) for field in fields}
        if any(field not in values for field in fields):
            self.on_missing(field_key)
        return template.format_map(supplied)
