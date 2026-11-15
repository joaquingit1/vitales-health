"""D9 object and replay privacy through the E05 fallback routers and durable ledger."""

from pathlib import Path

import pytest
from stack import Station, checked, refused

pytestmark = pytest.mark.trace("D9", "T28-09", "T28-15", "AGENTS-7")


@pytest.mark.parametrize("person", ["ilya", "tomas", "station", "ground"])
def test_ungranted_read_list_act_and_replay_leak_no_clinical_values(tmp_path: Path, person: str) -> None:
    station = Station(tmp_path)
    run = station.start()
    path = "/v1/plan-runs/" + str(run["plan_run_id"])
    assert checked(station.client.get(path)) == run
    done = checked(station.act(run, "step_done", step="icl10_note_episodes", key="owner_completion"))
    assert done["revision"] == 2
    before = station.effects()
    headers = {"X-Test-Person": person}
    code = "CONSENT_REQUIRED" if person == "ground" else "UNAUTHORIZED_SCOPE"
    responses = [station.client.get(path, headers=headers)]
    refused(responses[0], 403, code)
    listing = station.client.get("/v1/plan-runs?subject=mara", headers=headers)
    refused(listing, 403, "UNAUTHORIZED_SCOPE")
    responses.append(listing)
    for key in ("intruder_action", "owner_completion"):
        response = station.act(run, "step_done", step="icl10_note_episodes", key=key, person=person)
        refused(response, 403, "UNAUTHORIZED_SCOPE")
        responses.append(response)
    for response in responses:
        for secret in (
            str(run["plan_run_id"]),
            "icl10_note_episodes",
            "plan_icl10",
            '"items"',
            '"total"',
            '"count"',
        ):
            assert secret.encode() not in response.content, response.text
        assert response.json()["current_state"] is None
    assert station.effects() == before
