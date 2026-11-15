"""V81 through the documented E05 fallback, with no model or device providers."""

import json
from pathlib import Path
from typing import cast

import pytest
from stack import Station, checked, obj, rows, run_step

pytestmark = pytest.mark.trace("V81", "D34", "T28-24", "T28-31", "EI2", "EI5")


def test_link_fields_alone_change_and_offline_care_completes(tmp_path: Path) -> None:
    station = Station(tmp_path)
    views = []
    for status in ("up", "delayed", "down"):
        station.link.status = status
        response = station.client.get("/v1/conditions/ICL10/plan")
        assert response.status_code == 200, response.text
        view = response.json()
        ground = rows(view["stages"])[-1]
        assert ground["stage_id"] == "ground_log"
        del ground["link_text"], ground["leaves_at"]
        views.append(view)
    assert views[0] == views[1] == views[2]
    audit_before = len(station.query("platform_audit"))
    run = station.start()
    snapshots = [run]
    commands = 1
    run = checked(station.act(run, "step_done", step="icl10_note_episodes"))
    snapshots.append(run)
    commands += 1
    run = checked(station.act(run, "use_substitute", step="icl10_pulse_at_cameras"))
    snapshots.append(run)
    commands += 1
    assert run_step(run, "icl10_pulse_at_cameras")["state"] == "substituted"
    assert run_step(run, "icl10_pulse_at_oximeter")["state"] == "open"
    run = checked(station.act(run, "step_done", step="icl10_pulse_at_oximeter"))
    snapshots.append(run)
    commands += 1
    assert rows(run["rechecks"])
    run = checked(station.act(run, "escalate", step="icl10_worse_chest"))
    snapshots.append(run)
    commands += 1
    assert run["state"] == "escalated"
    assert len(station.help.receipts) == 1
    run = checked(station.act(run, "close", disposition="other"))
    snapshots.append(run)
    commands += 1
    assert run["state"] == "closed"
    assert run["allowed_actions"] == []
    assert len(station.query("platform_command_ledger")) == commands
    assert len(station.query("platform_audit")) - audit_before == commands
    assert len(station.query("plans_test_event")) == commands
    receipts = [obj(json.loads(cast(bytes, row[3]))) for row in station.query("platform_command_ledger")]
    assert {r["command_key"] for r in receipts} == {
        "start_acceptance",
        "step_done_icl10_note_episodes",
        "use_substitute_icl10_pulse_at_cameras",
        "step_done_icl10_pulse_at_oximeter",
        "escalate_icl10_worse_chest",
        "close_run",
    }
    for snapshot in snapshots:
        assert snapshot["state"] in {"active", "watching", "escalated", "closed"}
        for step in rows(snapshot["steps"]):
            assert step["state"] in {"open", "done", "substituted", "not_available"}
        assert not any("ground" in key and "wait" in key for key in snapshot)
