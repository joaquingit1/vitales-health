import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.units import Quantity, Unit
from crew_health.knowledge.adapters.release_codec import CanonicalReleaseCodec
from crew_health.knowledge.domain.export import ReviewExport
from crew_health.knowledge.module import setting_specs
from crew_health.platform.settings import specifications as platform_specifications


@pytest.fixture
def at() -> datetime:
    fixture = json.loads(Path("fixtures/knowledge/manifest.json").read_text())
    assert fixture["profile"] == "eval"
    return datetime.fromisoformat(fixture["clock_at"])


@pytest.fixture
def settings() -> MemorySettings:
    fixture = json.loads(Path("fixtures/knowledge/manifest.json").read_text())
    assert fixture["profile"] == "eval"
    values: dict[str, object] = {}
    for key, row in fixture["settings"].items():
        values[key] = Quantity(Decimal(row["value"]), Unit(row["unit"])) if row["unit"] else row["value"]
    shared = tuple(spec for spec in platform_specifications() if spec.key == "storage.max_rows")
    for spec in shared:
        if spec.maximum is None:
            raise ValueError("test storage bound missing")
        values[spec.key] = int(spec.maximum)
    return MemorySettings((*setting_specs(), *shared), values)


@pytest.fixture
def review_export(settings: MemorySettings) -> ReviewExport:
    fixture = json.loads(Path("fixtures/knowledge/shared-review-export.json").read_text(encoding="utf-8"))
    assert fixture["profile"] == "eval"
    return CanonicalReleaseCodec(settings=settings).decode_export(
        json.dumps(fixture["value"]).encode("utf-8")
    )
