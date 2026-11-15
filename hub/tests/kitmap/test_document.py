"""The kit-map document: schema, release checks B, 11, 12 and CAM, authoring reproducibility and EquipmentFacts."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from referencing import Registry, Resource

from crew_health.guidance.application.ports import EquipmentFacts, Known, Unknown
from crew_health.kitmap import module as kitmap_module
from crew_health.kitmap.application.ports import InstrumentReadinessPort, Snap
from crew_health.kitmap.domain.document import DocumentBounds, parse_kit_map
from crew_health.kitmap.domain.model import DocumentInvalid

from .conftest import CONFIG, EXAMPLE, ROOT, SETTINGS, FakeMission, Hub, edited, release, ui_copy_raw

pytestmark = [pytest.mark.trace("R44", "V79", "D31", "KM-I1", "KM-I10")]

WORDS = kitmap_module.vocabulary()
BOUNDS = DocumentBounds(
    **{key: cast(int, SETTINGS["kitmap." + key]) for key in DocumentBounds.__dataclass_fields__}
)
SCHEMAS = ROOT / "contracts/api/schemas"
CONTRACT_PATTERN = "^ICL[1-9][0-9]{0,2}$"
D22_PATTERN = "^ICL[1-9][0-9]{0,2}[A-Z]?$"
PROVENANCE = ROOT / "authoring/kitmap/provenance.json"


def parse(raw: bytes) -> object:
    return parse_kit_map(raw, BOUNDS, WORDS.environments, None)


def d22_schema(path: Path) -> dict[str, object]:
    """The package schema with only the condition ID pattern widened, in memory (coordinator decision D22,
    docs/requests/COORDINATOR-DECISIONS.md); contracts/ is not edited."""
    text = path.read_text(encoding="utf-8")
    return cast(
        dict[str, object],
        json.loads(text.replace(json.dumps(CONTRACT_PATTERN)[1:-1], json.dumps(D22_PATTERN)[1:-1])),
    )


def test_authored_config_validates_against_its_schema() -> None:
    registry: Registry = Registry().with_resources(
        (path.name, Resource.from_contents(d22_schema(path))) for path in SCHEMAS.glob("*.schema.json")
    )
    schema = d22_schema(SCHEMAS / "config-kit-map.schema.json")
    assert CONTRACT_PATTERN in (SCHEMAS / "config-kit-map.schema.json").read_text(encoding="utf-8")
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(json.loads(CONFIG.read_text(encoding="utf-8"))), key=str)
    assert errors == []


def test_authored_config_passes_every_release_check() -> None:
    doc = parse_kit_map(CONFIG.read_bytes(), BOUNDS, WORDS.environments, None)
    assert len(doc.conditions) == 119
    assert doc.example is False


def test_build_is_reproducible() -> None:
    """authoring/kitmap/build.py --check regenerates config/kit-map.json and provenance.json byte for byte."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "authoring/kitmap/build.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_row_cites_its_source_and_unchecked_rows_say_so() -> None:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    side = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    sources = (ROOT / "docs/product/sources.csv").read_text(encoding="utf-8")
    forms = {
        c["condition_id"]: c
        for c in json.loads(
            (ROOT / "docs/product/cliff/cliff-confirming-tests.json").read_text(encoding="utf-8")
        )
    }
    unchecked = set(side["unchecked_rows"])
    for row in doc["conditions"]:
        assert row["source_ids"] and all(s + "," in sources for s in row["source_ids"])
        assert row["cliff_url"] == forms[row["condition_id"]]["url"]
        assert row["review_state"] == "unreviewed"
        assert (row["checked_by"] is None) == (row["condition_id"] in unchecked)
    assert side["omitted_rows"] == []
    assert len(doc["conditions"]) + len(side["omitted_rows"]) == 119


@pytest.mark.parametrize(
    ("edit", "check_id"),
    [
        (lambda d: d["kits"]["station_leo"].append("tricorder"), "11a"),
        (lambda d: d["conditions"].append(dict(d["conditions"][0])), "11c"),
        (lambda d: d["conditions"][0].update(review_state="reviewed", checked_by=None), "12"),
        (lambda d: d["conditions"][0]["confirming_tests"][0]["instruments"].append("camera_station"), "CAM"),
        (lambda d: d["instruments"].append(dict(d["instruments"][0])), "11a"),
    ],
)
def test_release_checks_reject_with_findings(
    edit: Callable[[dict[str, object]], None], check_id: str
) -> None:
    with pytest.raises(DocumentInvalid) as raised:
        parse(edited(CONFIG, edit))
    assert check_id in {f.check_id for f in raised.value.findings}


def test_bounds_are_checked_first() -> None:
    small = dataclasses.replace(BOUNDS, max_conditions=10)
    with pytest.raises(DocumentInvalid) as raised:
        parse_kit_map(CONFIG.read_bytes(), small, WORDS.environments, None)
    assert {f.check_id for f in raised.value.findings} == {"B"}


@settings(max_examples=300, deadline=None)
@given(raw=st.binary(max_size=256))
def test_parser_never_throws_anything_but_document_invalid_on_bytes(raw: bytes) -> None:
    try:
        parse(raw)
    except DocumentInvalid:
        pass


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
    max_leaves=12,
)


@settings(max_examples=200, deadline=None)
@given(key=st.sampled_from(sorted(json.loads(EXAMPLE.read_text(encoding="utf-8")))), value=json_values)
def test_parser_never_throws_on_a_mutated_document(key: str, value: object) -> None:
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    doc[key] = value
    try:
        parse(json.dumps(doc).encode())
    except DocumentInvalid:
        pass


def test_equipment_facts_for_guidance(make_hub: Callable[..., Hub], config_raw: bytes) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()), mission=FakeMission("mars_surface"))
    facts = hub.resolver.require(EquipmentFacts)  # type: ignore[type-abstract]
    with hub.snapshot() as snap:
        # Packed is not usable now (E27 3.2, PLAN C3): with no readiness source the answer is unknown.
        assert facts.instruments_here(snap, None) == Unknown("equipment", "readiness_unknown")
        assert facts.instruments_here(snap, "loc_rover") == Unknown("equipment", "location_not_resolvable")
    ready = make_hub(
        release("rel_1", config_raw, ui_copy=ui_copy_raw()),
        mission=FakeMission("mars_surface"),
        ports={InstrumentReadinessPort: FakeReadiness(frozenset({"ultrasound", "oct"}))},
    )
    confirmed = ready.resolver.require(EquipmentFacts)  # type: ignore[type-abstract]
    with ready.snapshot() as snap:
        # Only what the readiness source confirms, and only what is packed here (no OCT on Mars).
        assert confirmed.instruments_here(snap, None) == Known(frozenset({"ultrasound"}))
    hub.mission.environment_id = None
    with hub.snapshot() as snap:
        assert facts.instruments_here(snap, None) == Unknown("equipment", "no_active_environment")
    broken = make_hub(
        release("rel_1", config_raw[:100], ui_copy=ui_copy_raw()), mission=FakeMission("mars_surface")
    )
    facts = broken.resolver.require(EquipmentFacts)  # type: ignore[type-abstract]
    with broken.snapshot() as snap:
        assert facts.instruments_here(snap, None) == Unknown("equipment", "kit_map_unavailable")
    no_mission = make_hub(release("rel_1", config_raw), with_mission=False)
    facts = no_mission.resolver.require(EquipmentFacts)  # type: ignore[type-abstract]
    with no_mission.snapshot() as snap:
        assert facts.instruments_here(snap, None) == Unknown("equipment", "no_mission_state")


@dataclasses.dataclass
class FakeReadiness:
    """InstrumentReadinessPort fake (E05 is not on main)."""

    usable: frozenset[str]

    def usable_now(self, snapshot: Snap, environment_id: str) -> frozenset[str]:
        return self.usable


def test_unparseable_active_release_fails_loudly_over_http(
    make_hub: Callable[..., Hub], config_raw: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw[:100], ui_copy=ui_copy_raw()))
    response = hub.client.get("/v1/kit-map", headers={"X-Test-Session-Role": "crew"})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR" and response.json()["retryable"] is True


@pytest.mark.parametrize(
    ("condition_id", "check_id"),
    [("ICL124A", "kitmap.11c"), ("ICL124a", "kitmap.1"), ("ICL124AB", "kitmap.1")],
)
def test_unknown_or_malformed_condition_ids_are_rejected(
    make_hub: Callable[..., Hub], config_raw: bytes, condition_id: str, check_id: str
) -> None:
    """D22 suffixes pass the pattern (ICL36A and ICL36B are in the authored map); an ID outside the 119 inventory
    fails CONTRACTS 11 check 11c, and anything else fails the schema."""

    def rename(doc: dict[str, object]) -> None:
        cast(list[dict[str, object]], doc["conditions"])[0]["condition_id"] = condition_id

    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()))
    assert not hub.activate(release("rel_2", edited(CONFIG, rename), ui_copy=ui_copy_raw()))
    assert check_id in {f.check_id for f in hub.rejected[-1]}
