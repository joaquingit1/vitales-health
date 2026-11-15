from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from crew_health.routine.domain.occurrence import Occurrence
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import ReplaceCadence, RoutineRelease, parse_release

REPO = Path(__file__).resolve().parents[3]
SCHEMAS = REPO / "contracts/api/schemas"
NOW = datetime(2026, 11, 14, tzinfo=UTC)


def document(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((REPO / "config" / f"{name}.json").read_text(encoding="utf-8")))


def example(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(
            (REPO / "contracts/api/examples/config" / f"{name}.example.json").read_text(encoding="utf-8")
        )["value"],
    )


def release() -> RoutineRelease:
    return parse_release(
        "rel_test",
        document("routine-schedule"),
        document("routine-rules"),
        example("ui-copy"),
        example("questionnaires"),
    )


def occurrence() -> Occurrence:
    r = release()
    return Occurrence(
        "ro_test",
        1,
        "p_test",
        r.items[0].item_id,
        "station_leo",
        1,
        "cadence",
        "cadence",
        True,
        None,
        None,
        None,
        None,
        "due",
        NOW,
        NOW + timedelta(days=1),
        NOW + timedelta(days=1),
        "all_day",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        r.release_id,
        "LIVE",
        "clock_1",
        NOW,
    )


def override() -> Override:
    r = release()
    binding = next(b for b in r.bindings if isinstance(b.effect, ReplaceCadence))
    item = r.item(binding.item_id)
    assert item is not None and isinstance(binding.effect, ReplaceCadence)
    assert binding.trigger_index is not None and binding.trigger_text is not None
    return Override(
        "rov_test",
        1,
        binding.item_id,
        None,
        binding.audience,
        binding.binding_id,
        binding.trigger_index,
        binding.trigger_text,
        "sensor_limit",
        "warning",
        "wrn_1",
        binding.effect.cadence,
        binding.trigger_text,
        binding.trigger_text,
        "active",
        NOW,
        "warning_cleared",
        None,
        r.notice,
        None,
        None,
        None,
        binding.basis,
        item.source_ids,
        r.release_id,
        ("p_test",),
        {"p_test": "site"},
        "LIVE",
        NOW,
        ("wrn_1",),
        binding.effect,
        False,
        None,
    )
