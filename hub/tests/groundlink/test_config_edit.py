import hashlib
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.groundlink.application.checks import LinkScheduleCheck
from crew_health.groundlink.application.config import LinkConfigCache
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease

from .support import Harness, hazards, lunar_window, profile_rows, profiles, release, validate_document


@pytest.mark.trace("R49", "E29-01")
def test_configuration_alone_changes_served_gap_and_delay(tmp_path: Path) -> None:
    data = profiles()
    hazard = hazards()
    lunar_window(data)
    for name, document in (
        ("config-environment-profiles.schema.json", data),
        ("config-environment-hazards.schema.json", hazard),
    ):
        validate_document(name, document)
    h = Harness(tmp_path, environment="lunar_surface", data=data)
    first = h.client.get("/v1/ground-link").json()
    assert first["gap_kind"] == "planned_line_of_sight"
    lunar_window(data, gap="relay_outage")
    validate_document("config-environment-profiles.schema.json", data)
    h.release = release(data, hazard)
    assert h.client.get("/v1/ground-link").json()["gap_kind"] == "relay_outage"
    lunar = next(p for p in profile_rows(data) if p["environment_id"] == "lunar_surface")
    cast(dict[str, object], lunar["comms_queue"])["simulated_one_way_delay_seconds"] = 42
    validate_document("config-environment-profiles.schema.json", data)
    h.release = release(data, hazard)
    changed = h.client.get("/v1/ground-link").json()
    assert changed["one_way_delay_s"] == 42 and changed["delay_text"] == first["delay_text"]


@pytest.mark.trace("R49", "E29-01")
@pytest.mark.parametrize("raw", [None, b"{", b"{}"])
def test_missing_or_corrupt_profiles_are_rejected(tmp_path: Path, raw: bytes | None) -> None:
    h = Harness(tmp_path)
    documents = dict(h.release.documents)
    del documents[ConfigName.ENVIRONMENT_PROFILES]
    if raw is not None:
        documents[ConfigName.ENVIRONMENT_PROFILES] = ConfigDocumentView(
            ConfigName.ENVIRONMENT_PROFILES, hashlib.sha256(raw).hexdigest(), raw
        )
    h.release = MemoryRelease("invalid_release", documents)
    response = h.client.get("/v1/ground-link")
    assert response.json()["code"] == "CONFIG_INVALID"
    with h.db.snapshot() as view, pytest.raises(ProblemError) as error:
        LinkConfigCache().get(view, "lunar_surface")
    assert error.value.code == ErrorCode.CONFIG_INVALID


@pytest.mark.trace("R49", "E29-01")
def test_loader_check_points_to_bad_window_and_missing_document_has_no_findings() -> None:
    data = profiles()
    lunar_window(data)
    lunar = next(p for p in profile_rows(data) if p["environment_id"] == "lunar_surface")
    link = cast(dict[str, object], lunar["link"])
    cast(list[dict[str, object]], link["sim_schedule"])[0]["planned"] = True
    cast(list[dict[str, object]], link["sim_schedule"])[0]["status"] = "up"
    check = LinkScheduleCheck()
    findings = check.check(CandidateRelease("release_1", release(data).documents))
    assert findings and findings[0].pointer.endswith("/link/sim_schedule/0")
    assert all(f.check_id == "groundlink.sim_schedule" for f in findings)
    assert check.check(CandidateRelease("release_1", {})) == ()
