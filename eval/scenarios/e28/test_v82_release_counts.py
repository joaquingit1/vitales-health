"""V82 real built documents and activation over the E05 fallback, not a full hub."""

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from crew_health.kernel.config import CandidateRelease, ConfigName
from crew_health.kernel.errors import ProblemError
from stack import ROOT, Station, checked, document, obj, rows, steps, view_step

pytestmark = pytest.mark.trace("V82", "D32", "T28-20", "T28-36", "T28-41", "PR-I3")


def test_shipped_counts_match_authoring_subprocess_and_all_environment_views(tmp_path: Path) -> None:
    station = Station(tmp_path)
    plans = rows(station.raw(ConfigName.ONBOARD_PLANS)["plans"])
    counts = Counter(str(plan["written_for"]) for plan in plans)
    assert counts == {"condition": 7, "family": 112, "flow": 20}
    kit = station.raw(ConfigName.KIT_MAP)
    conditions = rows(kit["conditions"])
    assert len(conditions) == 119
    assert len(obj(kit["kits"])) == 4
    result = subprocess.run(
        [sys.executable, str(ROOT / "authoring/plans/check.py")],
        cwd=ROOT,
        env={**os.environ, "UV_CACHE_DIR": "/private/tmp/vitales-e28-uv-cache"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"rows (\d+); plans (\d+); aboard in all environments (\d+) of (\d+)", result.stdout)
    assert match is not None, result.stdout
    assert tuple(map(int, match.groups())) == (len(conditions), len(plans), len(conditions), len(conditions))
    for environment in obj(kit["kits"]):
        station.mission.environment = environment
        for row in conditions:
            response = station.client.get("/v1/conditions/" + str(row["condition_id"]) + "/plan")
            assert response.status_code == 200, (row["condition_id"], environment, response.text)
            assert response.json()["plan_aboard"] is True, (row["condition_id"], environment)


def test_officer_only_candidate_rejected_live_exact_sim_gap_keeps_last_good(tmp_path: Path) -> None:
    station = Station(tmp_path)
    raw = station.raw(ConfigName.ONBOARD_PLANS)
    plan = next(plan for plan in rows(raw["plans"]) if plan["plan_id"] == "plan_icl10")
    plan["stages"] = [
        stage for stage in rows(plan["stages"]) if stage["stage_id"] in {"medical_officer", "ground_log"}
    ]
    # Connect the scratch row through the contract's explicit plan_id, not an inferred name.
    kit = station.raw(ConfigName.KIT_MAP)
    for row in rows(kit["conditions"]):
        row["plan_id"] = "plan_" + str(row["condition_id"]).lower()
    candidate = dict(station.documents)
    candidate[ConfigName.ONBOARD_PLANS] = document(ConfigName.ONBOARD_PLANS, raw)
    candidate[ConfigName.KIT_MAP] = document(ConfigName.KIT_MAP, kit)
    station.mission.mode = "LIVE"
    findings = station.check.check(CandidateRelease("release_officer", candidate))
    assert any("ICL10" in f.message and "lunar_surface" in f.message for f in findings)
    short_rows = {
        finding.pointer.split("/")[2] for finding in findings if finding.pointer.startswith("/conditions/")
    }
    assert short_rows == {"ICL10"}
    assert (len(rows(kit["conditions"])) - len(short_rows), len(rows(kit["conditions"]))) == (118, 119)
    with station.db.begin(actor=station.editor.actor, correlation_id="officer_propose") as uow:
        validation = station.activation.propose(
            uow, station.editor, CandidateRelease("release_officer", candidate)
        )
    assert not validation.passed
    with station.db.begin(actor=station.editor.actor, correlation_id="officer_activate") as uow:
        revision = station.activation.revision(uow, "release_officer")
        with pytest.raises(ProblemError) as error:
            station.activation.activate(
                uow, station.editor, release_id="release_officer", expected_etag=revision.etag
            )
        assert error.value.code.value == "INVALID_TRANSITION"
    station.mission.mode = "SIM"
    assert station.check.check(CandidateRelease("release_officer", candidate)) == ()
    assert station.check.warning_count == len(obj(kit["kits"]))
    with station.db.snapshot() as snapshot:
        assert snapshot.release.release_id == "release_e28_shipped"
    response = station.client.get("/v1/conditions/ICL10/plan")
    assert response.status_code == 200, response.text
    assert response.json()["release_id"] == "release_e28_shipped"


def test_changed_label_activates_new_views_but_restart_keeps_run_pins(tmp_path: Path) -> None:
    station = Station(tmp_path)
    run = station.start()
    original = station.client.get("/v1/conditions/ICL10/plan").json()
    raw = station.raw(ConfigName.ONBOARD_PLANS)
    step = next(step for step in steps(raw) if step["step_id"] == "icl10_note_episodes")
    old = step["text"]
    step["text"] = str(old) + " (SIM release edit)"
    station.install("release_e28_label", {ConfigName.ONBOARD_PLANS: raw})
    updated = station.client.get("/v1/conditions/ICL10/plan")
    assert updated.status_code == 200, updated.text
    assert view_step(updated.json(), str(step["step_id"]))["text"] == step["text"]
    assert view_step(original, str(step["step_id"]))["text"] == old
    station.restart()
    pinned = checked(station.client.get("/v1/plan-runs/" + str(run["plan_run_id"])))
    assert pinned["config_pins"] == run["config_pins"]
    done = checked(station.act(pinned, "step_done", step=str(step["step_id"])))
    assert done["config_pins"] == run["config_pins"]
    with station.db.snapshot() as snapshot:
        cached = snapshot.connection.rows("SELECT plan_json FROM plans_parsed_plan", (), limit=10)
    assert any(str(old) in str(row[0]) for row in cached)
    assert all("SIM release edit" not in str(row[0]) for row in cached)
