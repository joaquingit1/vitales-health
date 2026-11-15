"""Basis construction, logical identity, keys and ids (E22 3.1, DE22-3; T-E22-004, 018, 019, 039)."""

from __future__ import annotations

import re
import uuid
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.ids import ID_PATTERN
from crew_health.participation.domain.errors import InvalidBasis
from crew_health.participation.domain.identity import (
    award_id,
    canonical_json,
    completion_id,
    correction_identity,
    intent_key,
    logical_identity,
    payload_hash,
    reconcile_identity,
    replacement_identity,
)
from crew_health.participation.domain.types import CompletionBasis, is_id

from .harness import PROP, at_day, basis

pytestmark = pytest.mark.E22

ids = st.from_regex(ID_PATTERN, fullmatch=True)


@pytest.mark.trace("T-E22-004", "INV-E22-01")
def test_basis_field_set_equals_the_design_and_an_extra_field_fails() -> None:
    assert {f.name for f in fields(CompletionBasis)} == {
        "basis", "basis_ref", "patient_id", "mission_id", "origin_mode", "occurred_at", "original_offset",
        "actor_person_id", "cause_kind", "cause_ref", "required_answers_complete",
    }  # fmt: skip
    with pytest.raises(TypeError):
        CompletionBasis(
            **{**{f.name: getattr(basis(), f.name) for f in fields(CompletionBasis)}, "systolic": 120}
        )  # type: ignore[call-arg]
    with pytest.raises(AttributeError):
        basis().symptom_count = 3  # type: ignore[misc]


@pytest.mark.trace("T-E22-004")
@pytest.mark.parametrize(
    "bad",
    [
        {"ref": "bad id"},
        {"patient": ""},
        {"actor": "not valid!"},
        {"when": datetime(2026, 11, 2, 12, 0)},
        {"when": datetime(2026, 11, 2, 12, 0, tzinfo=timezone(timedelta(hours=2)))},
        {"offset": "0200"},
        {"offset": "+25:00"},
        {"kind": "concern_reported", "cause_kind": "answer", "cause_ref": "a1"},
        {"kind": "concern_reported", "complete": True},
        {"kind": "episode_updated", "cause_kind": "answer", "cause_ref": "bad ref"},
    ],
)
def test_malformed_basis_is_refused_at_construction(bad: dict[str, object]) -> None:
    with pytest.raises(InvalidBasis):
        basis(**bad)  # type: ignore[arg-type]


@pytest.mark.trace("T-E22-004")
def test_unknown_enum_values_are_refused() -> None:
    good = basis()
    with pytest.raises(InvalidBasis):
        replace(good, basis="journal_entry")  # type: ignore[arg-type]
    with pytest.raises(InvalidBasis):
        replace(good, origin_mode="REAL")  # type: ignore[arg-type]
    with pytest.raises(InvalidBasis):
        replace(basis("episode_updated"), cause_kind="value")  # type: ignore[arg-type]
    with pytest.raises(InvalidBasis):
        replace(basis("episode_updated"), cause_kind=None)
    with pytest.raises(InvalidBasis):
        replace(good, required_answers_complete=None)
    assert is_id("abc") and not is_id(3)


@pytest.mark.trace("T-E22-039", "R31")
def test_unknown_offset_stays_none_and_is_never_synthesized() -> None:
    unknown = basis(offset=None)
    assert unknown.original_offset is None
    assert basis(offset="-03:30").original_offset == "-03:30"
    assert payload_hash(unknown) != payload_hash(basis(offset="+00:00"))
    assert payload_hash(unknown) == payload_hash(basis(offset=None))


@pytest.mark.trace("T-E22-018", "INV-E22-03", "DE22-3")
@PROP
@given(a=st.tuples(ids, ids, ids, ids), b=st.tuples(ids, ids, ids, ids))
def test_ids_with_separators_never_collide_for_different_identities(
    a: tuple[str, str, str, str], b: tuple[str, str, str, str]
) -> None:
    def key(parts: tuple[str, str, str, str]) -> str:
        return intent_key(["episode_updated", *parts])

    assert (key(a) == key(b)) == (a == b)


@pytest.mark.trace("T-E22-018")
def test_separator_characters_in_ids_cannot_merge_two_identities() -> None:
    one = intent_key(["daily_check", "a:b", "c"])
    two = intent_key(["daily_check", "a", "b:c"])
    three = intent_key(["daily_check", "a.b", "c"])
    assert len({one, two, three}) == 3
    left = basis("daily_check", ref="a-b", patient="c")
    right = basis("daily_check", ref="a", patient="b-c")
    assert intent_key(logical_identity(left)) != intent_key(logical_identity(right))


@pytest.mark.trace("T-E22-018", "T-E22-025")
def test_logical_identity_per_basis_and_special_identities() -> None:
    assert logical_identity(basis("daily_check", ref="d1", patient="mara")) == ["daily_check", "d1", "mara"]
    assert logical_identity(basis("concern_reported", ref="e1", actor="mara")) == [
        "concern_reported",
        "e1",
        "mara",
    ]
    episode = basis("episode_updated", ref="e1", cause_kind="urgent_event", cause_ref="u1", actor=None)
    assert logical_identity(episode) == ["episode_updated", "e1", "mara", "urgent_event", "u1"]
    keys = {
        intent_key(replacement_identity("lin", 1)),
        intent_key(replacement_identity("lin", 2)),
        intent_key(correction_identity("lin", "r1")),
        intent_key(reconcile_identity("clock", "r1")),
    }
    assert len(keys) == 4


@pytest.mark.trace("T-E22-019", "DE22-3")
def test_same_identity_same_key_changed_payload_changes_only_the_hash() -> None:
    first = basis(when=at_day(3))
    again = basis(when=at_day(3))
    changed = basis(when=at_day(4))
    assert intent_key(logical_identity(first)) == intent_key(logical_identity(changed))
    assert payload_hash(first) == payload_hash(again)
    assert payload_hash(first) != payload_hash(changed)
    assert payload_hash(first) != payload_hash(basis(when=at_day(3), origin="LIVE"))


@pytest.mark.trace("T-E22-006", "DE22-3")
@PROP
@given(text=st.text(min_size=1, max_size=40))
def test_completion_id_is_a_version_5_uuid_and_stable(text: str) -> None:
    key = intent_key(["daily_check", text, "mara"])
    value = completion_id(key)
    assert value == completion_id(key)
    parsed = uuid.UUID(value)
    assert parsed.version == 5
    assert parsed == uuid.uuid5(
        uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), "crew-health:participation:completion:" + key
    )
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value)


@pytest.mark.trace("T-E22-024", "INV-E22-10")
def test_award_id_is_deterministic_and_distinct_per_person_origin_threshold() -> None:
    base = award_id("mara", "demo-mission", "SIM", 7)
    assert re.fullmatch(r"pat_[0-9a-f]{24}", base)
    assert base == award_id("mara", "demo-mission", "SIM", 7)
    others = {
        award_id("tomas", "demo-mission", "SIM", 7),
        award_id("mara", "demo-mission", "LIVE", 7),
        award_id("mara", "demo-mission", "SIM", 14),
        award_id("mara", "other", "SIM", 7),
    }
    assert base not in others and len(others) == 4


@pytest.mark.trace("T-E22-121")
def test_canonical_json_is_sorted_compact_and_refuses_floats() -> None:
    assert canonical_json({"b": [1, None, True], "a": "é"}) == '{"a":"é","b":[1,null,true]}'
    with pytest.raises(InvalidBasis):
        canonical_json([1.5])
    with pytest.raises(InvalidBasis):
        canonical_json({"x": float("nan")})
