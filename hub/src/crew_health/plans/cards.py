"""Offline print rendering, based only on validated document bytes and packed equipment."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path

import jsonschema
from pydantic import BaseModel

from crew_health.contracts_gen.schemas.config_kit_map_schema import KitMapJson
from crew_health.contracts_gen.schemas.config_onboard_plans_schema import OnboardPlansJson
from crew_health.contracts_gen.schemas.config_ui_copy_schema import UiCopyJson
from crew_health.kernel.config import ConfigDocumentView, ConfigName
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.plans.application.release import (
    UiCopy,
    array_value,
    kit_for,
    kit_supplies,
    object_value,
    packed_stock,
)
from crew_health.plans.application.settings import BOUNDS
from crew_health.plans.domain.availability import EnvContext, assess_step
from crew_health.plans.domain.model import Plan, PlansDocument, Step, parse_document
from crew_health.plans.domain.view import view_stages


@dataclass(frozen=True)
class DiskDocuments:
    documents: Mapping[ConfigName, ConfigDocumentView]
    release_id: str = "print"

    def document(self, name: ConfigName) -> ConfigDocumentView:
        return self.documents[name]

    def version_of(self, name: ConfigName) -> str:
        return self.documents[name].version


def load(directory: Path) -> DiskDocuments:
    documents = {}
    models: tuple[tuple[ConfigName, type[BaseModel]], ...] = (
        (ConfigName.ONBOARD_PLANS, OnboardPlansJson),
        (ConfigName.KIT_MAP, KitMapJson),
        (ConfigName.UI_COPY, UiCopyJson),
    )
    for name, model in models:
        path = directory / (name.value + ".json")
        # Same document bound as PlansSettings's permitted ceiling, before parsing or allocating content.
        if path.stat().st_size > dict(BOUNDS)["max_document_bytes"]:
            raise ValueError("document exceeds bound")
        raw = path.read_bytes()
        value = object_value(json.loads(raw))
        if "value" in value:
            value = object_value(value["value"])
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # D22 widens only condition IDs until the coordinator regenerates the package models.
        schema = json.loads(
            json.dumps(model.model_json_schema()).replace(
                "^ICL[1-9][0-9]{0,2}$", "^ICL[1-9][0-9]{0,2}[A-Z]?$"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(value)
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return DiskDocuments(documents)


def word(copy: UiCopy, section: str, key: str) -> str:
    result = copy.text(section, key)
    if not result:
        raise ValueError("missing print words")
    return result


def shell(title: str, content: str) -> str:
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>'
        + escape(title)
        + "</title><style>body{font:16px sans-serif;max-width:70em;margin:2em auto}"
        "li{margin-bottom:1em}section{break-inside:avoid}@media print{body{margin:0;font-size:11pt}"
        "a{color:inherit;text-decoration:none}}</style><body>" + content + "</body></html>\n"
    )


def step_html(step: Step, plan: Plan, context: EnvContext, copy: UiCopy) -> str:
    assessment = assess_step(step, plan, context)
    who = step.who.kind if step.who.kind != "skill" else "skill:" + (step.who.skill_id or "")
    needs = list(step.needs.instruments) + list(step.needs.skills)
    needs += [
        n.supply_id
        + (" " + str(n.per_use.value) + " " + n.per_use.unit.code if n.per_use is not None else "")
        for n in step.needs.supplies
    ]
    pieces = [
        '<li id="'
        + escape(step.step_id, quote=True)
        + '" data-availability="'
        + escape(assessment.availability, quote=True)
        + '"><p>'
        + escape(step.text)
        + "</p>",
        "<p>" + escape(who) + "</p><p>" + escape(", ".join(needs)) + "</p>",
    ]
    if step.text_first_time:
        pieces.append("<p>" + escape(step.text_first_time) + "</p>")
    substitute = plan.step(step.substitute_step_id or "")
    if substitute is not None:
        pieces.append(
            "<p>"
            + escape(copy.render(word(copy, "plan_lines", "substituted"), {"step": substitute.text}))
            + "</p>"
        )
    if assessment.availability == "not_aboard":
        pieces.append("<p>" + escape(word(copy, "plan_lines", "not_aboard")) + "</p>")
    elif assessment.availability == "substituted" and substitute is None:
        raise ValueError("missing substitute")
    if step.cannot_replace_text:
        pieces.append("<p>" + escape(step.cannot_replace_text) + "</p>")
    if step.source_ids:
        # Catalog IDs only (docs/product/sources.csv), so the card names no label of its own.
        pieces.append('<p class="sources">' + escape(" ".join(step.source_ids)) + "</p>")
    return "".join(pieces) + "</li>"


def card(plan: Plan, document: PlansDocument, context: EnvContext, copy: UiCopy) -> str:
    # "packed" is the contract's basis identifier, not a claim about live inventory or crew skills.
    parts = [
        "<h1>" + escape(plan.title) + "</h1><p>" + escape(document.notice) + "</p>",
        "<p>" + escape(document.version + " sha256:" + document.document_sha256) + "</p>",
        '<p data-basis="packed">' + escape(context.environment_id + ": packed") + "</p>",
    ]
    for stage in view_stages(plan, context):
        parts.append("<section><h2>" + escape(word(copy, "plan_stage_words", stage.stage_id)) + "</h2>")
        if stage.stage_id == "ground_log":
            parts.append("<p>" + escape(word(copy, "plan_lines", "ground_log_row")) + "</p>")
            parts.extend("<p>" + escape(i.text) + "</p>" for i in plan.ground_log_items)
        else:
            if stage.stage_id == "return" and plan.return_stage is not None:
                parts.extend("<p>" + escape(c.text) + "</p>" for c in plan.return_stage.criteria)
            parts.append("<ol>" + "".join(step_html(s, plan, context, copy) for s in stage.steps) + "</ol>")
        parts.append("</section>")
    return shell(plan.title, "".join(parts))


def render(directory: Path, environment: str) -> dict[str, str]:
    release = load(directory)
    raw = release.document(ConfigName.ONBOARD_PLANS)
    plans = parse_document(
        object_value(json.loads(raw.raw)), document_sha256=raw.version, supplies=kit_supplies(release)
    )
    if plans.rejected_plan_ids or plans.rejected_step_ids or len(plans.plans) > 1000:
        raise ValueError("invalid plans document")
    kit = object_value(json.loads(release.document(ConfigName.KIT_MAP).raw))
    kits = object_value(kit["kits"])
    if environment not in kits:
        raise ValueError("unknown environment")
    copy = UiCopy(object_value(json.loads(release.document(ConfigName.UI_COPY).raw)))
    context = EnvContext(
        environment,
        kit_for(release, environment),
        packed_stock(release).get(environment),
        None,
        None,
        {},
        True,
    )
    order = [
        str(row.get("plan_id") or "plan_" + str(row["condition_id"]).lower())
        for v in array_value(kit["conditions"])
        if (row := object_value(v))
    ]
    ordered = sorted(
        plans.plans, key=lambda p: (order.index(p.plan_id) if p.plan_id in order else len(order), p.plan_id)
    )
    result: dict[str, str] = {}
    links = []
    for plan in ordered:
        if not re.fullmatch(r"plan_[a-z0-9_]{1,59}", plan.plan_id) or len(plan.steps) > 200:
            raise ValueError("invalid card identity or step bound")
        if environment not in plan.environments:
            continue
        filename = plan.plan_id + ".html"
        result[filename] = card(plan, plans, context, copy)
        links.append('<li><a href="' + escape(filename, quote=True) + '">' + escape(plan.title) + "</a></li>")
    result["index.html"] = shell(
        plans.version, "<p>" + escape(plans.notice) + "</p><ol>" + "".join(links) + "</ol>"
    )
    return result


def write_cards(directory: Path, environment: str, output: Path) -> None:
    try:
        rendered = render(directory, environment)
    except (OSError, ValueError, KeyError, TypeError, jsonschema.ValidationError) as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "invalid print documents") from error
    output.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        (output / name).write_bytes(content.encode("utf-8"))
