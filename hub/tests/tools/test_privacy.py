from __future__ import annotations

import pytest

from crew_health.tools.domain.arguments import JsonBounds, parse_json
from crew_health.tools.domain.errors import ToolsInvalid


@pytest.mark.trace("T-E12-001", "T-E12-005", "I2")
@pytest.mark.parametrize("raw", [b'{"x":1e-9999}', b'{"x":1e9999}', b'{"state_snapshot_version":true}'])
def test_numeric_boundary_never_fabricates_zero_or_coerces_boolean(raw: bytes) -> None:
    from crew_health.tools.adapters.jsonschema_validator import ActionValidator

    bounds = JsonBounds(8192, 16, 50, 2000)
    if b"true" in raw:
        raw = b'{"action":"assessment","reason":"reason","decision_affected":null,"state_snapshot_version":true,"assessment_id":"assessment_1"}'
        with pytest.raises(ToolsInvalid):
            ActionValidator(bounds).parse(raw)
    else:
        with pytest.raises(ToolsInvalid):
            parse_json(raw, bounds)
