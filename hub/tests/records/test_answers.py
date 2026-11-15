"""T-E01-07 R06: pinned question rules retain null semantics and estimate lineage."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease, MemoryUnits
from crew_health.kernel.units import Quantity, Unit
from crew_health.records.domain.answers import load_questions, validate_answer

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-07", "R06")]


def release():
    raw = json.dumps(
        json.loads(Path("contracts/api/examples/config/questionnaires.example.json").read_text())["value"]
    ).encode()
    document = ConfigDocumentView(ConfigName.QUESTIONNAIRES, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release", {document.name: document})


def units():
    return MemoryUnits(
        {
            "1": ("scalar", Decimal(1), Decimal(0)),
            "h": ("time", Decimal(3600), Decimal(0)),
            "min": ("time", Decimal(60), Decimal(0)),
        }
    )


def test_confirmation_and_correction_have_distinct_values():
    question = load_questions(release())["sleep_estimate"]
    assert validate_answer(question, presence="present", value="right", units=units()) == "right"
    corrected = validate_answer(
        question, presence="present", value=Quantity(Decimal(90), Unit("min")), units=units()
    )
    assert corrected == Quantity(Decimal("1.5"), Unit("h"))
    with pytest.raises(ProblemError):
        validate_answer(question, presence="present", value="correct", units=units())


@pytest.mark.parametrize("presence", ["unknown", "declined"])
def test_unknown_and_declined_require_null(presence):
    question = load_questions(release())["symptom_new"]
    assert validate_answer(question, presence=presence, value=None, units=units()) is None
    with pytest.raises(ProblemError):
        validate_answer(question, presence=presence, value=False, units=units())


def test_scale_rejects_bool_fraction_and_bad_unit():
    question = load_questions(release())["mood"]
    for value in (True, Quantity(Decimal("0.1"), Unit("1")), Quantity(Decimal(1), Unit("h"))):
        with pytest.raises(ProblemError):
            validate_answer(question, presence="present", value=value, units=units())


def test_choices_require_matching_presence():
    question = load_questions(release())["symptom_new"]
    assert validate_answer(question, presence="absent", value="nothing_new", units=units()) == "nothing_new"
    with pytest.raises(ProblemError):
        validate_answer(question, presence="present", value="nothing_new", units=units())


SCALE_QUESTION = load_questions(release())["mood"]


@given(st.decimals(allow_nan=False, allow_infinity=False, min_value=-100, max_value=100))
def test_scale_checked_independently(value):
    question = SCALE_QUESTION
    if value == value.to_integral_value() and 0 <= value <= 10:
        assert (
            validate_answer(
                question, presence="present", value=Quantity(value, Unit("1")), units=units()
            ).value
            == value
        )
    else:
        with pytest.raises(ProblemError):
            validate_answer(question, presence="present", value=Quantity(value, Unit("1")), units=units())


def test_configuration_edit_changes_question_without_code():
    initial = release()
    raw = json.loads(initial.document(ConfigName.QUESTIONNAIRES).raw)
    raw["questions"][2]["text"] = "Changed configured question"
    changed_raw = json.dumps(raw).encode()
    document = ConfigDocumentView(
        ConfigName.QUESTIONNAIRES, hashlib.sha256(changed_raw).hexdigest(), changed_raw
    )
    changed = MemoryRelease("changed", {document.name: document})
    assert load_questions(initial)["mood"].text != load_questions(changed)["mood"].text
