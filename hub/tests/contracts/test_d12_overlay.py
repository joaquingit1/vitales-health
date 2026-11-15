"""D12, SYSTEM 5.4 and 6.5: effective changes never mutate the guarded base."""

import json
from pathlib import Path

import pytest

from crew_health.contracts_gen.build import effective_tree, verify_base, verify_overlay
from crew_health.contracts_gen.enums import ConfigName

pytestmark = [pytest.mark.E01, pytest.mark.trace("D12", "SYSTEM-5.4", "SYSTEM-6.5")]
ROOT = Path(__file__).resolve().parents[3]


def test_effective_enum_and_index_bounds_preserve_base(tmp_path: Path) -> None:
    base = verify_base(ROOT)
    overlay = verify_overlay(ROOT, base)
    assert overlay is not None
    api = effective_tree(ROOT, overlay, overlay.effective(base), tmp_path)
    common = json.loads((api / "schemas/common.schema.json").read_bytes())
    original = json.loads((ROOT / "contracts/api/schemas/common.schema.json").read_bytes())
    assert common["$defs"]["config_name"]["enum"] == [
        *original["$defs"]["config_name"]["enum"],
        "unit-mapping",
    ]
    assert ConfigName("unit-mapping").name == "UNIT_MAPPING"
    index = json.loads((api / "schemas/config-index.schema.json").read_bytes())
    old_index = json.loads((ROOT / "contracts/api/schemas/config-index.schema.json").read_bytes())
    assert index["properties"]["documents"]["minItems"] == 19
    assert index["properties"]["documents"]["maxItems"] == 19
    assert old_index["properties"]["documents"]["minItems"] == 18
    assert old_index["properties"]["documents"]["maxItems"] == 18
    assert verify_base(ROOT) == base


@pytest.mark.parametrize(
    "pointer,expected",
    [("/$defs/absent", 18), ("/$defs/config_name/enum", []), ("/$defs/~2bad", 18), ("", 18)],
)
def test_json_pointer_edit_rejects_missing_stale_or_invalid_target(pointer: str, expected: object) -> None:
    from crew_health.contracts_gen.build import replace_pointer

    with pytest.raises(ValueError):
        replace_pointer(
            (ROOT / "contracts/api/schemas/common.schema.json").read_bytes(),
            {"pointer": pointer, "expected": expected, "value": 19},
        )
