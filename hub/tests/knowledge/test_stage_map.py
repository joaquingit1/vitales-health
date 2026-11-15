import json
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemorySettings
from crew_health.knowledge.adapters.stage_file import load_stage_map
from crew_health.knowledge.domain.catalog import CaptureFacts
from crew_health.knowledge.domain.errors import EvidenceInvalid


@pytest.mark.trace("T-E10-08", "T-E10-34", "T-E10-55", "I3")
def test_configured_ceilings_only_narrow_capture_facts(settings: MemorySettings) -> None:
    raw = Path("knowledge/inputs/stage-map.json").read_bytes()
    stages = load_stage_map(raw, settings=settings)
    assert stages.ceiling(CaptureFacts("http_fetch", "text/plain", False, True, None)) == "partial"
    assert stages.ceiling(CaptureFacts("http_fetch", "application/pdf", True, False, None)) == "inaccessible"
    value = json.loads(raw)
    value["methods"][0]["ceiling"] = "summary_only"
    changed = load_stage_map(json.dumps(value).encode("utf-8"), settings=settings)
    assert changed.ceiling(CaptureFacts("http_fetch", "text/plain", True, False, None)) == "summary_only"
    assert stages.ceiling(CaptureFacts("http_fetch", "text/plain", True, False, None)) == "full"
    value["methods"].append(value["methods"][0])
    with pytest.raises(EvidenceInvalid):
        load_stage_map(json.dumps(value).encode("utf-8"), settings=settings)
