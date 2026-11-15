"""Package examples and typed helpers for domain tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.domain.availability import EnvContext, Person, StockLevel
from crew_health.plans.domain.model import Plan, PlansDocument, SupplyInfo, parse_document


def obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [obj(item) for item in value]


def raw_document() -> dict[str, object]:
    path = Path(__file__).resolve().parents[3] / "docs/product/api/examples/config/onboard-plans.example.json"
    return obj(obj(json.loads(path.read_text(encoding="utf-8")))["value"])


def supplies() -> dict[str, SupplyInfo]:
    return {
        sid: SupplyInfo(sid, Unit("1"), consumable)
        for sid, consumable in (
            ("eye_shield", True),
            ("eye_rinse_water", True),
            ("eye_wash_set", False),
            ("towels", False),
        )
    }


def document(raw: Mapping[str, object] | None = None) -> PlansDocument:
    return parse_document(
        raw_document() if raw is None else raw, document_sha256="document-hash", supplies=supplies()
    )


def plan(pid: str = "plan_icl10") -> Plan:
    result = document().plan(pid)
    assert result is not None
    return result


def context() -> EnvContext:
    return EnvContext(
        "lunar_surface",
        frozenset(("camera_station", "pulse_oximeter", "bp_cuff", "wrist_band")),
        {sid: StockLevel(sid, True, Quantity(Decimal(10), info.unit)) for sid, info in supplies().items()},
        (
            Person("subject", "Subject", 0, None, True, {}),
            Person("helper", "Helper", 1, 1, True, {"pulse_check_wrist": "fresh"}),
        ),
        "subject",
        {},
        True,
    )


def now() -> datetime:
    return datetime(2026, 11, 14, 13, 37, tzinfo=UTC)
