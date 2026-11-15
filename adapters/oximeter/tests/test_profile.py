"""V75 validates content, safe atomic reload and acquisition profile pinning."""

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from jsonschema.exceptions import ValidationError

from crew_health_device.adapters.profile_file import load_document, load_profile_file
from crew_health_device.application.health import manifest
from crew_health_device.application.profiles import ProfileStore
from crew_health_device.domain.errors import ProfileError
from crew_health_device.domain.profile import DeviceProfile, parse_profile
from crew_health_device.domain.quality import Accepted, Refused, Sample, assess

from .helpers import Clock, component_document, profile, profile_document, validate


@pytest.mark.trace("V75", "AGENTS 1.1")
def test_config_edit_changes_manifest_label_and_same_sample_verdict() -> None:
    original = profile_document()
    changed = deepcopy(original)
    changed["device_label"] = "Paired PLX model label"
    changed["steady_reading"] = True
    validate(original, "device-profile")
    validate(changed, "device-profile")
    before, after = parse_profile(original), parse_profile(changed)
    report = manifest(
        after,
        family="oximeter",
        service="0x1822",
        characteristics=["0x2A5E"],
        source_origin="LIVE",
        connected_state="disconnected",
    )
    assert cast(dict[str, object], report["profile"])["device_label"] == "Paired PLX model label"
    clock = Clock()
    samples = [Sample(Decimal(v), clock.now(), n) for v, n in ((97, 0), (98, 1000000000))]
    assert isinstance(assess(samples, before.components[0], 1, steady=before.steady_reading), Accepted)
    assert assess(samples, after.components[0], 1, steady=after.steady_reading) == Refused("not_steady")


@pytest.mark.trace("V75")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("min_samples", True),
        ("min_samples", 0),
        ("components", []),
        ("provenance", []),
        ("hardware_evidence", "invented"),
        ("device_label", ""),
        ("unknown", "x"),
    ],
)
def test_schema_rejects_invalid_profile_shape(key: str, value: object) -> None:
    document = profile_document()
    document[key] = value
    with pytest.raises(ValidationError):
        validate(document, "device-profile")


@pytest.mark.trace("V75")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("range_min", "100"),
        ("range_max", "NaN"),
        ("range_min", "Infinity"),
        ("range_min", "nope"),
        ("range_min", 0),
        ("saturation_low", "-1"),
        ("saturation_high", "101"),
        ("range_min", None),
        ("max_rate_per_second", "0"),
        ("max_rate_per_second", "-1"),
        ("units", {}),
        ("units", {"percent": ""}),
        ("units", []),
        ("field", ""),
        ("indicator_code", 3),
    ],
)
def test_parser_rejects_invalid_component_invariants(key: str, value: object) -> None:
    document = profile_document()
    component_document(document)[key] = value
    with pytest.raises(ProfileError) as failure:
        parse_profile(document)
    assert failure.value.pointer.startswith("/")
    assert failure.value.reason


@pytest.mark.trace("V75")
@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate_field",
        "duplicate_indicator",
        "overlap",
        "measured",
        "saturation_order",
        "bad_components",
        "bad_provenance",
        "bad_provenance_item",
        "bad_status",
        "bad_minimum",
        "empty_calibration",
    ],
)
def test_parser_rejects_cross_field_and_missing_invariants(mutation: str) -> None:
    document = profile_document()
    if mutation == "missing":
        del document["model"]
    elif mutation in ("duplicate_field", "duplicate_indicator"):
        other = deepcopy(component_document(document))
        other["indicator_code" if mutation == "duplicate_field" else "field"] = "another"
        document["components"] = [component_document(document), other]
    elif mutation == "overlap":
        document["noted_status"] = document["rejecting_status"]
    elif mutation == "measured":
        document["hardware_evidence"] = "measured"
    elif mutation == "saturation_order":
        component_document(document).update(saturation_low="90", saturation_high="80")
    elif mutation == "bad_components":
        document["components"] = ["x"]
    elif mutation == "bad_provenance":
        document["provenance"] = []
    elif mutation == "bad_provenance_item":
        document["provenance"] = ["x"]
    elif mutation == "bad_status":
        document["noted_status"] = [1]
    elif mutation == "bad_minimum":
        document["min_samples"] = 0
    else:
        document["calibration"] = ""
    with pytest.raises(ProfileError):
        parse_profile(document)


@pytest.mark.trace("V75")
def test_valid_saturation_and_sourced_measured_evidence_are_preserved() -> None:
    doc = profile_document()
    doc.update(hardware_evidence="measured", evidence_ref="paired-run-1", model="paired-model")
    component_document(doc).update(saturation_low="0", saturation_high="100", max_rate_per_second="2.5")
    validate(doc, "device-profile")
    parsed = parse_profile(doc)
    assert parsed.evidence_ref == "paired-run-1"
    assert parsed.components[0].max_rate_per_second == Decimal("2.5")
    assert parsed.rule_for_indicator("missing") is None


@pytest.mark.trace("V75")
def test_invalid_reload_keeps_last_good_profile_and_valid_reload_activates() -> None:
    candidate = profile()

    def loader() -> DeviceProfile:
        if candidate.version == "invalid":
            raise ProfileError("/", "invalid")
        return candidate

    store = ProfileStore(loader)
    candidate = replace(candidate, version="invalid")
    assert not store.reload()
    assert store.current().version == "1"
    candidate = replace(candidate, version="2")
    assert store.reload()
    assert store.current().version == "2"


@pytest.mark.trace("V75")
def test_profile_loader_validates_schema_limits_and_initial_failure(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile_document()), encoding="utf-8")
    assert load_profile_file(path, 8192) == profile()
    with pytest.raises(ProfileError, match="oversize"):
        load_profile_file(path, 1)
    for content in ("[]", "{bad", "\u00ff", '{"device_label":"only"}'):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ProfileError):
            load_profile_file(path, 8192)
    path.write_bytes(b"\xff")
    with pytest.raises(ProfileError):
        load_document(path, 8192)
    with pytest.raises(ProfileError):
        ProfileStore(lambda: load_profile_file(tmp_path / "absent", 8192))


@pytest.mark.trace("V75")
def test_deeply_nested_invalid_reload_keeps_last_good_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile_document()), encoding="utf-8")
    store = ProfileStore(lambda: load_profile_file(path, 10000))
    path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    assert not store.reload()
    assert store.current() == profile()
