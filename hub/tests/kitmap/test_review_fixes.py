"""Independent review findings, ported as tests: Q26 regions, check 11c, sibling documents, decimal quantities."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.testing import MemoryRelease
from crew_health.kitmap.application.ports import (
    CoverChange,
    CoverPlanningPort,
    KitCapabilitiesPort,
    KitMapContext,
    SupplyAddition,
)

from .conftest import CONFIG, ROOT, SESSION_HEADER, FakeMission, Hub, documents, release, ui_copy_raw, view

pytestmark = [pytest.mark.trace("R44", "V79", "KM-I6", "S8.2")]

PROVENANCE = ROOT / "authoring/kitmap/provenance.json"

# Coordinator decision Q26 (docs/requests/COORDINATOR-DECISIONS.md): a form region maps to a body_region value only
# when it lies within it. Expected values below are read from Q26's own list, not from the build's table.
Q26_GOLDEN = {
    "ICL23": ("brain", {"head"}),
    "ICL33": ("finger joints", {"hand"}),
    "ICL52": ("cervical spine", {"neck"}),
    "ICL57": ("thoracic or lumbar", {"back"}),
    "ICL70": ("mouth", {"head"}),
    "ICL72": ("cervical radiculopathy and lumbar radiculopathy", {"neck", "back"}),
    "ICL74": ("ear nose and throat", {"ear", "head", "neck"}),
    "ICL83": ("brain", {"head"}),
    "ICL105": ("throat", {"neck"}),
    "ICL107": ("lower extremity", {"legs"}),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.mark.parametrize("condition_id", sorted(Q26_GOLDEN))
def test_q26_regions_checked_against_the_form(condition_id: str) -> None:
    region, expected = Q26_GOLDEN[condition_id]
    side = {r["condition_id"]: r for r in json.loads(PROVENANCE.read_text(encoding="utf-8"))["rows"]}[
        condition_id
    ]
    quote = next(q for q in side["body_region_quotes"] if q["region"] == region)
    form = json.loads((ROOT / f"docs/product/cliff/forms/{condition_id}.json").read_text(encoding="utf-8"))
    page = next(p["text"] for p in form["pages"] if p["page"] == quote["page"])
    assert _norm(quote["quote"]) in _norm(page)  # the region really is on the form
    rows = {c["condition_id"]: c for c in json.loads(CONFIG.read_text(encoding="utf-8"))["conditions"]}
    assert expected <= set(rows[condition_id]["body_regions"])


def test_release_check_without_a_condition_registry_is_a_finding(
    make_hub: Callable[..., Hub], config_raw: bytes
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()), registry=False)
    assert not hub.activate(release("rel_2", config_raw, ui_copy=ui_copy_raw()))
    assert "kitmap.11c" in {f.check_id for f in hub.rejected[-1]}


@pytest.mark.parametrize(
    ("name", "document"), [(ConfigName.UI_COPY, "ui-copy"), (ConfigName.SKILLS_CATALOG, "skills")]
)
def test_unparseable_sibling_documents_are_findings_not_exceptions(
    make_hub: Callable[..., Hub], config_raw: bytes, name: ConfigName, document: str
) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()))
    docs = {**documents(config_raw, ui_copy=ui_copy_raw()), name: view(name, b"{not json")}
    staged = CandidateRelease("rel_2", docs)
    findings = [f for check in hub.loader_checks() for f in check.check(staged)]
    assert findings and all(f.document is name for f in findings)
    assert not hub.activate(MemoryRelease("rel_2", docs))


@dataclass
class FakeCover:
    """CoverPlanningPort fake (E32 is not on main)."""

    def cover_changed(
        self, ctx: KitMapContext, *, add_supplies: Sequence[SupplyAddition]
    ) -> Sequence[CoverChange]:
        return [CoverChange(s.supply_id, 1, 2) for s in add_supplies]


@pytest.mark.parametrize("quantity", ["2.50", "0.000001", "1000000"])
def test_what_if_quantity_keeps_its_decimal_text(
    make_hub: Callable[..., Hub], example_raw: bytes, quantity: str
) -> None:
    hub = make_hub(
        release("rel_1", example_raw, ui_copy=ui_copy_raw()),
        mission=FakeMission("station_leo", "SIM"),
        ports={CoverPlanningPort: FakeCover()},
    )
    supply = json.loads(example_raw)["supplies"][0]["supply_id"]
    body = '{"add_supplies":[{"supply_id":' + json.dumps(supply) + ',"quantity":' + quantity + "}]}"
    response = hub.client.post(
        "/v1/kit-map/what-if",
        content=body.encode("utf-8"),
        headers={SESSION_HEADER: "crew", "content-type": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert re.search(r'"quantity":' + re.escape(quantity) + r"[,}]", response.text), response.text


def test_capabilities_take_the_release_from_the_snapshot(
    make_hub: Callable[..., Hub], config_raw: bytes
) -> None:
    """E27 4.2 and E05 2: capabilities(snapshot, environment_id); the release is the snapshot's own."""
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()))
    port = hub.resolver.require(KitCapabilitiesPort)  # type: ignore[type-abstract]
    with hub.snapshot() as snap:
        mars = {c.code: c for c in port.capabilities(snap, "mars_surface")}
        assert mars["ultrasound_imaging"].kit_status == "available"
    assert list(inspect.signature(KitCapabilitiesPort.capabilities).parameters) == [
        "self",
        "snapshot",
        "environment_id",
    ]
