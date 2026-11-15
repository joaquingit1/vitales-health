from __future__ import annotations

import copy
import csv
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest

from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.package_checks import ENVIRONMENTS, package_checks, saved_form_pages
from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.common_schema import EnvironmentId
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding, ReleaseCheck

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-08", "R06", "V75")]
ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "docs/product"
type Documents = dict[ConfigName, Any]

# The one finding the package example pair genuinely carries: plan_icl64's vehicle CO2 step cites C25, a technical
# brief with `page: null`, as `replaces_ground_task`; check 14 wants every form quote on a cited saved form page.
FIRST_START_FINDINGS = {
    "14": {
        Finding(
            "14",
            ConfigName.ONBOARD_PLANS,
            "/plans/6/stages/0/steps/1/replaces_ground_task",
            "form quote cites no page of a saved form",
        )
    }
}


PACKAGE_PAIR = frozenset({ConfigName.KIT_MAP, ConfigName.ONBOARD_PLANS})


def first_start_documents() -> Documents:
    """config/ documents with the package's kit-map and onboard-plans examples, which agree with each other (see
    tests/app/conftest.py): the targeted edits below name steps of the package example."""
    documents: Documents = {}
    for name in ConfigName:
        configured = ROOT / "config" / (name.value + ".json")
        if name not in PACKAGE_PAIR and configured.is_file():
            documents[name] = json.loads(configured.read_bytes())
        elif name.value in {"environment-hazards", "routine-schedule"}:
            documents[name] = json.loads((PRODUCT / (name.value + ".json")).read_bytes())
        elif name == ConfigName.KIT_MAP:
            # D22: config/kit-map.json fails check 1 on main, so the package example stands in.
            documents[name] = json.loads((PRODUCT / "api/examples/kit-map.example.json").read_bytes())
        else:
            example = PRODUCT / "api/examples/config" / (name.value + ".example.json")
            documents[name] = json.loads(example.read_bytes())["value"]
    return documents


def release(documents: Documents) -> CandidateRelease:
    views = {}
    for name, value in documents.items():
        raw = json.dumps(value, ensure_ascii=False).encode()
        views[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease("release_package_checks", views)


def condition_ids() -> frozenset[str]:
    with (PRODUCT / "condition-assessments-119.csv").open(encoding="utf-8-sig", newline="") as handle:
        return frozenset(row["condition_id"] for row in csv.DictReader(handle))


def form_pages() -> dict[str, tuple[str, ...]]:
    return dict(
        saved_form_pages(path.read_bytes()) for path in sorted((PRODUCT / "cliff/forms").glob("*.json"))
    )


@pytest.fixture(scope="module")
def checks() -> dict[str, ReleaseCheck]:
    built = package_checks(condition_ids=condition_ids(), form_pages=form_pages())
    return {check.check_id: check for check in built}


@pytest.fixture(scope="module")
def baseline() -> Documents:
    return first_start_documents()


def test_one_check_per_required_contributor_not_supplied_elsewhere(checks: dict[str, ReleaseCheck]) -> None:
    assert set(checks) == required_contributors() - {"1", "2", "9", "13"}


def test_environment_vocabulary_matches_the_contract() -> None:
    assert ENVIRONMENTS == get_args(EnvironmentId.model_fields["root"].annotation)


@pytest.mark.parametrize("check_id", sorted(required_contributors() - {"1", "2", "9", "13"}))
def test_first_start_release(checks: dict[str, ReleaseCheck], baseline: Documents, check_id: str) -> None:
    findings = checks[check_id].check(release(baseline))
    if check_id == "6":
        assert findings and {finding.check_id for finding in findings} == {"6.unbound_trigger"}
    else:
        assert set(findings) == FIRST_START_FINDINGS.get(check_id, set())


def _plan_step(documents: Documents, step_id: str) -> tuple[str, dict[str, Any]]:
    for p, plan in enumerate(documents[ConfigName.ONBOARD_PLANS]["plans"]):
        for k, stage in enumerate(plan["stages"]):
            for s, step in enumerate(stage.get("steps", [])):
                if step["step_id"] == step_id:
                    return f"/plans/{p}/stages/{k}/steps/{s}", step
    raise LookupError(step_id)


def _index(items: list[dict[str, Any]], key: str, value: str) -> int:
    return next(i for i, item in enumerate(items) if item[key] == value)


def _set(name: ConfigName, path: str, value: object, expect: str | None = None) -> Callable[[Documents], str]:
    def edit(documents: Documents) -> str:
        *parents, last = path.strip("/").split("/")
        target: Any = documents[name]
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[int(last) if isinstance(target, list) else last] = value
        return expect or path

    return edit


def _medicine_without_onboard_words(documents: Documents) -> str:
    step = documents[ConfigName.RESPONDER_FLOWS]["flows"][0]["treatment_steps"][4]
    step["step"], step["kit"] = "Airway device and medicines", "the airway kit"
    return "/flows/0/treatment_steps/4"


def _append_amount(documents: Documents) -> str:
    pointer, step = _plan_step(documents, "icl10_note_episodes")
    step["text"] += " Take 5 mg."
    return pointer + "/text"


def _medical_officer_in_do_now(documents: Documents) -> str:
    pointer, step = _plan_step(documents, "icl10_note_episodes")
    step["who"] = "crew_medical_officer"
    return pointer + "/who"


def _swap_first_stages(documents: Documents) -> str:
    stages = documents[ConfigName.ONBOARD_PLANS]["plans"][0]["stages"]
    stages[0], stages[1] = stages[1], stages[0]
    return "/plans/0/stages/1/stage_id"


def _change_form_quote(documents: Documents) -> str:
    pointer, step = _plan_step(documents, "eye_followup_check")
    step["replaces_ground_task"]["quote"] = "Ground support is never needed."
    return pointer + "/replaces_ground_task/quote"


def _substitute_not_substitute_only(documents: Documents) -> str:
    _, substitute = _plan_step(documents, "icl10_pulse_at_oximeter")
    substitute["substitute_only"] = False
    pointer, _ = _plan_step(documents, "icl10_pulse_at_cameras")
    return pointer + "/substitute_step_id"


def _assistant_raises_act(documents: Documents) -> str:
    definitions = documents[ConfigName.WARNING_RULES]["definitions"]
    index = _index(definitions, "raised_by", "assistant")
    definitions[index]["level"] = "act"
    return f"/definitions/{index}/level"


def _choice_opens_missing_flow(documents: Documents) -> str:
    questions = documents[ConfigName.QUESTIONNAIRES]["questions"]
    index = _index(questions, "id", "red_chest")
    questions[index]["choices"][0]["flow_id"] = "no_such_flow"
    return f"/questions/{index}/choices/0/flow_id"


def _unknown_kit_instrument(documents: Documents) -> str:
    kit = documents[ConfigName.KIT_MAP]["kits"]["station_leo"]
    kit.append("no_such_instrument")
    return f"/kits/station_leo/{len(kit) - 1}"


def _plan_drops_transit(documents: Documents) -> str:
    plans = documents[ConfigName.ONBOARD_PLANS]["plans"]
    plans[_index(plans, "plan_id", "plan_icl14")]["environments"].remove("transit")
    rows = documents[ConfigName.KIT_MAP]["conditions"]
    return f"/conditions/{_index(rows, 'condition_id', 'ICL14')}"


def _count_step_without_seconds(documents: Documents) -> str:
    skills = documents[ConfigName.SKILLS_CATALOG]["skills"]
    s = _index(skills, "skill_id", "pulse_check_wrist")
    t = _index(skills[s]["teach_steps"], "kind", "count")
    skills[s]["teach_steps"][t]["count_seconds"] = None
    return f"/skills/{s}/teach_steps/{t}/count_seconds"


def _routine_skill_missing(documents: Documents) -> str:
    items = documents[ConfigName.ROUTINE_SCHEDULE]["items"]
    index = next(i for i, item in enumerate(items) if item.get("skill_ids"))
    items[index]["skill_ids"][0] = "no_such_skill"
    return f"/items/{index}/skill_ids/0"


def _exclusion_becomes_preference(documents: Documents) -> str:
    eligibility = documents[ConfigName.ROLE_RULES]["eligibility"]
    eligibility[_index(eligibility, "rule_id", "never_subject")]["kind"] = "prefer"
    return "/eligibility"


FL, SL, WR = ConfigName.RESPONDER_FLOWS, ConfigName.SENSOR_LIMITS, ConfigName.WARNING_RULES
MUTATIONS: list[tuple[str, Callable[[Documents], str]]] = [
    ("3", _set(FL, "/triage/0/answers/0/goes_to", "no_such_node")),
    ("3", _set(FL, "/flows/0/immediate_steps/0", "Give 5 mg at once.")),
    ("3", _medicine_without_onboard_words),
    ("4", _set(SL, "/limits/0/hazard_level/value", 4)),
    ("4", _set(SL, "/limits/1/card_mark", True)),
    ("4", _set(SL, "/limits/0/value_from", "pending", "/limits/0/value")),
    ("5", _set(WR, "/definitions/0/condition/limit_id", "co2_7d_watch", "/limits/0")),
    ("5", _assistant_raises_act),
    ("6", _set(ConfigName.ROUTINE_RULES, "/bindings/0/binds/trigger_text", "Cabin CO2 is high")),
    ("7", _set(ConfigName.ENVIRONMENT_PROFILES, "/profiles/0/sensor_type_ids/0", "no_such_sensor")),
    ("8", _set(ConfigName.QUESTIONNAIRES, "/daily_check/question_ids/0", "no_such_question")),
    ("8", _choice_opens_missing_flow),
    ("10", _set(ConfigName.HELP_PRESENTATION, "/start_node_id", "no_such_node")),
    ("10", _set(ConfigName.HELP_PRESENTATION, "/answer_log/0/answer_index", 99)),
    ("11", _set(ConfigName.KIT_MAP, "/conditions/1/condition_id", "ICL10")),
    ("11", _unknown_kit_instrument),
    ("12.sensor", _set(SL, "/limits/0/unit", "mm[Hg]")),
    ("12.effect", _set(ConfigName.ENVIRONMENT_PROFILES, "/effect_filters/0/effect_starts_with", "Lunar")),
    (
        "12.kit",
        _set(ConfigName.KIT_MAP, "/conditions/0/review_state", "reviewed", "/conditions/0/checked_by"),
    ),
    ("14", _medical_officer_in_do_now),
    ("14", _append_amount),
    ("14", _swap_first_stages),
    ("14", _change_form_quote),
    ("14", _substitute_not_substitute_only),
    ("15", _plan_drops_transit),
    ("16.skills", _set(ConfigName.SKILLS_CATALOG, "/skills/0/teach_steps/0/step_no", 2)),
    ("16.skills", _count_step_without_seconds),
    ("16.skills", _routine_skill_missing),
    (
        "16.roles",
        _set(ConfigName.ROLE_RULES, "/medical_officer_cascade/0/rank", 2, "/medical_officer_cascade"),
    ),
    ("16.roles", _exclusion_becomes_preference),
    ("16.roles", _set(ConfigName.ROLE_RULES, "/flow_roles/0/flow_id", "no_such_flow")),
]


@pytest.mark.parametrize(("check_id", "mutate"), MUTATIONS)
def test_targeted_edit_fails_its_check(
    checks: dict[str, ReleaseCheck], baseline: Documents, check_id: str, mutate: Callable[[Documents], str]
) -> None:
    documents = copy.deepcopy(baseline)
    pointer = mutate(documents)
    found = set(checks[check_id].check(release(documents))) - FIRST_START_FINDINGS.get(check_id, set())
    assert found and {(finding.check_id, finding.pointer) for finding in found} >= {(check_id, pointer)}


def test_plan_finding_names_row_and_environment(checks: dict[str, ReleaseCheck], baseline: Documents) -> None:
    documents = copy.deepcopy(baseline)
    _plan_drops_transit(documents)
    assert [finding.message for finding in checks["15"].check(release(documents))] == [
        "ICL14 has no plan aboard in transit"
    ]


def test_d22_suffixed_condition_is_valid(checks: dict[str, ReleaseCheck], baseline: Documents) -> None:
    documents = copy.deepcopy(baseline)
    documents[ConfigName.KIT_MAP]["conditions"][0]["condition_id"] = "ICL36A"
    assert checks["11"].check(release(documents)) == ()


def test_unbound_trigger_is_a_warning_and_check_15_only_warns_outside_live(
    checks: dict[str, ReleaseCheck], baseline: Documents
) -> None:
    registry = LoaderChecks(maximum=64)
    for check in checks.values():
        registry.register(check)
    loader = ConfigLoader(registry, max_document_bytes=8388608)
    documents = copy.deepcopy(baseline)
    _plan_drops_transit(documents)
    for origin in ("LIVE", "REPLAY", "SIM"):
        result = loader.validate(release(documents), origin=origin)
        warned = {finding.check_id for finding in result.warnings}
        rejected = {finding.check_id for finding in result.errors}
        assert "6.unbound_trigger" in warned and "6.unbound_trigger" not in rejected
        assert ("15" in rejected) == (origin == "LIVE") and ("15" in warned) == (origin != "LIVE")


@pytest.mark.parametrize("broken", [None, [], {"unexpected": True}])
def test_missing_or_malformed_document_is_a_finding_never_an_exception(
    checks: dict[str, ReleaseCheck], baseline: Documents, broken: object
) -> None:
    for name in ConfigName:
        documents = copy.deepcopy(baseline)
        if broken is None:
            del documents[name]
        else:
            documents[name] = broken
        for check in checks.values():
            findings = check.check(release(documents))
            assert all(finding.check_id.split(".")[0] == check.check_id.split(".")[0] for finding in findings)


def test_saved_form_pages_rejects_misnumbered_pages() -> None:
    raw = (PRODUCT / "cliff/forms/ICL48.json").read_bytes()
    url, pages = saved_form_pages(raw)
    assert url.endswith("eye-foreign-body-cliff-2023.pdf") and len(pages) > 8
    form = json.loads(raw)
    form["pages"][0]["page"] = 2
    with pytest.raises(ConfigInvalid):
        saved_form_pages(json.dumps(form).encode())
