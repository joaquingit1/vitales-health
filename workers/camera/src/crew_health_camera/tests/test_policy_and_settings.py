"""The policy and settings documents: schema, provenance, rejection of missing or corrupt documents, and a content
edit that changes the output with no code change (AGENTS rule 1, V75)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from crew_health_camera.adapters.settings import DEFAULT_POLICY, DEFAULT_SETTINGS, load_policy, load_settings
from crew_health_camera.adapters.sim_source import Scene
from crew_health_camera.domain.policy import REASON_CODES, PolicyInvalid, parse_policy
from crew_health_camera.tests.support import measurement, policy_document, rig

SCHEMA = DEFAULT_POLICY.parent / "camera-quality.schema.json"


@pytest.mark.trace("AGENTS-1.1", "V75")
def test_shipped_policy_matches_its_schema_and_every_limit_has_a_basis() -> None:
    document = policy_document()
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(document)
    quality = parse_policy(document)
    assert set(quality.reasons) == set(REASON_CODES)
    assert quality.rules_for("pulse") is quality.pulse.signal and quality.rules_for("spo2") is None


def _mutations() -> list[tuple[str, Any]]:
    return [
        ("/frames/min_luma/basis", ""),
        ("/frames/min_luma/value", -1),
        ("/frames/min_luma/value", True),
        ("/frames/min_luma/value", "40"),
        ("/frames/min_luma", 40),
        ("/pulse/band_hz/min", 0),
        ("/pulse/band_hz/max", True),
        ("/pulse/band_hz/max", 0.5),
        ("/pulse/edge_margin_hz/value", 0.9),
        ("/breathing/code", "pulse"),
        ("/reasons/too_dark", ""),
        ("/reasons/made_up", "x"),
        ("/notes/selected", "{device} {patient}"),
        ("/version", 3),
        ("/frames", []),
    ]


def _apply(document: dict[str, Any], pointer: str, value: Any) -> dict[str, Any]:
    edited = copy.deepcopy(document)
    *parents, last = pointer.strip("/").split("/")
    target = edited
    for key in parents:
        target = target[key]
    target[last] = value
    return edited


@pytest.mark.trace("AGENTS-1.1", "V75")
@pytest.mark.parametrize(("pointer", "value"), _mutations())
def test_corrupt_policy_refuses_startup(pointer: str, value: Any) -> None:
    with pytest.raises(PolicyInvalid):
        parse_policy(_apply(policy_document(), pointer, value))


@pytest.mark.trace("AGENTS-1.1")
@given(key=st.sampled_from(["policy_id", "frames", "pulse", "breathing", "selection", "reasons", "notes"]))
def test_policy_missing_any_section_refuses_startup(key: str) -> None:
    document = policy_document()
    del document[key]
    with pytest.raises(PolicyInvalid):
        parse_policy(document)


@pytest.mark.trace("AGENTS-1.1")
def test_missing_or_unreadable_documents_refuse_startup(tmp_path: Path) -> None:
    with pytest.raises(PolicyInvalid):
        load_policy(tmp_path / "absent.json")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(PolicyInvalid):
        load_policy(tmp_path / "broken.json")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(PolicyInvalid):
        load_settings(tmp_path / "list.json")


@pytest.mark.trace("AGENTS-1.1", "V75", "D08")
def test_reason_and_threshold_edits_in_a_scratch_policy_change_the_attempt(tmp_path: Path) -> None:
    document = policy_document()
    document["reasons"]["too_much_movement"] = "please hold still"
    path = tmp_path / "camera-quality.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    moving = {"camera_1": Scene(pulse_hz=1.6, breath_hz=None, head_motion=0.08)}
    shipped = rig(moving)
    shipped.service.start(measurement())
    shipped.service.run_next()
    assert shipped.hub.calls[0][1]["failure_reason"] == "too much movement"
    edited = rig(moving, quality=load_policy(path))
    edited.service.start(measurement())
    edited.service.run_next()
    assert edited.hub.calls[0][1]["failure_reason"] == "please hold still"
    document["frames"]["max_face_motion"]["value"] = 0.2
    path.write_text(json.dumps(document), encoding="utf-8")
    tolerant = rig(moving, quality=load_policy(path))
    tolerant.service.start(measurement())
    tolerant.service.run_next()
    assert tolerant.hub.calls[0][1]["outcome"] == "success"


@pytest.mark.trace("AGENTS-6.bounds")
def test_settings_load_with_bounds_and_refuse_unbounded_or_unsourced_values(tmp_path: Path) -> None:
    settings = load_settings(DEFAULT_SETTINGS)
    assert settings.bounds.max_acquisitions >= 1 and settings.hub_timeout_ms >= 1
    document = json.loads(DEFAULT_SETTINGS.read_text(encoding="utf-8"))
    for key, value in (
        ("max_frames", {"value": 0, "basis": "x"}),
        ("max_devices", {"value": 2}),
        ("extra", {}),
    ):
        broken = dict(document)
        broken[key] = value
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(PolicyInvalid):
            load_settings(path)
