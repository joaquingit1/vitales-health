import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.contracts_gen.build import effective_tree, verify_base, verify_overlay

ROOT = Path(__file__).resolve().parents[3]
OLD = r"^ICL[1-9][0-9]{0,2}$"
NEW = r"^ICL[1-9][0-9]{0,2}[A-Z]?$"
pytestmark = [pytest.mark.E01, pytest.mark.trace("D22", "D12", "SYSTEM.6.5")]


def patterns(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "pattern" and isinstance(child, str):
                yield child
            else:
                yield from patterns(child)
    elif isinstance(value, list):
        for child in value:
            yield from patterns(child)


def test_every_condition_pattern_changes_only_in_the_effective_tree(tmp_path: Path) -> None:
    base = verify_base(ROOT)
    overlay = verify_overlay(ROOT, base)
    assert overlay is not None
    api = effective_tree(ROOT, overlay, overlay.effective(base), tmp_path)
    occurrences = 0
    for original in sorted((ROOT / "contracts/api/schemas").glob("*.json")):
        before = list(patterns(json.loads(original.read_bytes())))
        if OLD not in before:
            continue
        count = before.count(OLD)
        occurrences += count
        after = list(patterns(json.loads((api / "schemas" / original.name).read_bytes())))
        assert after == [NEW if pattern == OLD else pattern for pattern in before]
        for condition in ("ICL1", "ICL36", "ICL36A", "ICL36B", "ICL119"):
            assert all(re.fullmatch(pattern, condition) for pattern in after if pattern == NEW)
        for invalid in ("ICL0", "ICL036B", "ICL1000", "ICL36AA", "ICL36b", " ICL36B", "ICL36B\n"):
            assert re.fullmatch(NEW, invalid) is None
    assert occurrences == 11
    assert verify_base(ROOT) == base


def test_measurement_model_action_accepts_the_nasa_variant_without_opening_other_fields(
    tmp_path: Path,
) -> None:
    base = verify_base(ROOT)
    overlay = verify_overlay(ROOT, base)
    assert overlay is not None
    api = effective_tree(ROOT, overlay, overlay.effective(base), tmp_path)
    documents = {path.name: json.loads(path.read_bytes()) for path in (api / "schemas").glob("*.json")}
    registry: Registry = Registry().with_resources(
        (name, Resource.from_contents(document)) for name, document in documents.items()
    )
    validator = Draft202012Validator(documents["model-action.schema.json"], registry=registry)
    action = {
        "action": "request_measurement",
        "reason": "Verify the selected condition",
        "decision_affected": None,
        "state_snapshot_version": 1,
        "capability_code": "temperature",
        "body_region": None,
        "condition_ids": ["ICL36B"],
    }
    assert validator.is_valid(action)
    assert not validator.is_valid({**action, "invented_result": "measured"})
    assert not validator.is_valid({**action, "condition_ids": ["ICL36BB"]})


@pytest.mark.parametrize("pointer,expected,value", [("/rows/0/pattern", "old", "new"), ("/rows/1", 7, 8)])
def test_overlay_json_pointer_replaces_existing_array_members_only(
    pointer: str, expected: object, value: object
) -> None:
    from crew_health.contracts_gen.build import replace_pointer

    raw = b'{"rows":[{"pattern":"old"},7],"0":"object-key"}'
    changed = json.loads(replace_pointer(raw, {"pointer": pointer, "expected": expected, "value": value}))
    assert (
        changed == {"rows": [{"pattern": "new"}, 7], "0": "object-key"}
        if pointer.endswith("pattern")
        else changed == {"rows": [{"pattern": "old"}, 8], "0": "object-key"}
    )


@pytest.mark.parametrize("token", ["-", "-1", "01", "+1", "2", "1.0", " 1", "١", "9" * 10000])
def test_overlay_json_pointer_rejects_noncanonical_or_missing_array_indexes(token: str) -> None:
    from crew_health.contracts_gen.build import replace_pointer

    with pytest.raises(ValueError):
        replace_pointer(b'{"rows":[0,1]}', {"pointer": "/rows/" + token, "expected": 1, "value": 2})


@given(st.lists(st.integers(-100, 100), min_size=1, max_size=8), st.integers(0, 100))
def test_array_pointer_changes_exactly_the_selected_element(values: list[int], position: int) -> None:
    from crew_health.contracts_gen.build import replace_pointer

    index = position % len(values)
    raw = json.dumps({"rows": values, "0": "kept"}).encode("utf-8")
    original = list(values)
    changed = json.loads(
        replace_pointer(
            raw, {"pointer": "/rows/" + str(index), "expected": values[index], "value": values[index] + 1}
        )
    )
    expected = list(values)
    expected[index] += 1
    assert changed == {"rows": expected, "0": "kept"}
    assert values == original
