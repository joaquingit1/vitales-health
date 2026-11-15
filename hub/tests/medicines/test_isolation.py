"""INV-M7 (E32 3.4): every dosing read filters to the current mission and the mission mode, so a record of another
mission or mode is invisible and therefore missing, never substituted. One database holds every partition; switching
the mission facts back shows nothing was lost."""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_health.supplies.application.ports import MissionFacts

from .harness import SUBJECT, Station, build_station, headers, lunar
from .test_flow import CLIENT_TIME, _confirm, _context, _diagnose, _post

APAP_LOT = "lot-apap-001"


def _other_mission() -> MissionFacts:
    base = lunar()
    return MissionFacts("mission_2", base.mission_mode, base.active_environment_id, 180, "node_1")


def _proposal(st: Station, diagnosis: str) -> dict[str, object]:
    response = _post(
        st,
        "/v1/dose-proposals",
        {
            "working_diagnosis_id": diagnosis,
            "product_id": "acetaminophen_325_tab",
            "client_time": CLIENT_TIME,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _lot(st: Station, lot_id: str) -> dict[str, object]:
    response = st.client.get("/v1/medicines/lots", headers=headers())
    assert response.status_code == 200, response.text
    (row,) = [x for x in response.json()["lots"] if x["lot_id"] == lot_id]
    return dict(row)


def _administrations(st: Station) -> list[object]:
    response = st.client.get(f"/v1/medication-administrations?subject={SUBJECT}", headers=headers())
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _seed_partition_a(st: Station) -> str:
    """Context, a confirmed administration from lot-apap-001 and then an adverse inspection of that lot."""
    _context(st)
    diagnosis = _diagnose(st)
    proposal = _proposal(st, diagnosis)
    assert proposal["outcome"] == "proposal" and proposal["lot_id"] == APAP_LOT
    confirmed = _confirm(st, proposal)
    assert confirmed.status_code == 201, confirmed.text
    adverse = _post(
        st,
        "/v1/medicines/inspections",
        {"lot_id": APAP_LOT, "finding": "discoloration", "client_time": CLIENT_TIME},
    )
    assert adverse.status_code == 201, adverse.text
    return diagnosis


def _assert_partition_a(st: Station, diagnosis: str) -> None:
    assert len(_administrations(st)) == 1
    lot = _lot(st, APAP_LOT)
    assert lot["quantity_now"] == {"value": 28, "ucum": "{tbl}"}
    assert lot["excluded_by"] != []
    again = _proposal(st, diagnosis)  # the earlier dose counts here: the interval has not passed
    assert again["outcome"] == "refusal"
    assert again["refusal"]["code"] != "patient_context_missing"  # type: ignore[index]


def _assert_fresh_partition(st: Station, diagnosis_of_a: str) -> None:
    """Nothing of partition A reaches the dose: its diagnosis, context, administration and inspection are missing."""
    assert (
        _post(
            st,
            "/v1/dose-proposals",
            {
                "working_diagnosis_id": diagnosis_of_a,
                "product_id": "acetaminophen_325_tab",
                "client_time": CLIENT_TIME,
            },
        ).status_code
        == 404
    )
    assert _administrations(st) == []
    lot = _lot(st, APAP_LOT)
    assert lot["quantity_now"] == {"value": 30, "ucum": "{tbl}"} and lot["excluded_by"] == []
    diagnosis = _diagnose(st)
    no_allergies = _proposal(st, diagnosis)  # the allergy list of partition A is missing here
    assert no_allergies["outcome"] == "refusal"
    assert no_allergies["refusal"]["code"] == "allergy_list_unknown"  # type: ignore[index]
    allergies = _post(
        st,
        f"/v1/people/{SUBJECT}/medication-context",
        {"part": "allergies", "state": "none_known", "client_time": CLIENT_TIME},
    )
    assert allergies.status_code == 201, allergies.text
    missing = _proposal(st, diagnosis)  # and so are its other parts
    assert missing["outcome"] == "refusal"
    assert missing["refusal"]["code"] == "patient_context_missing"  # type: ignore[index]
    _context(st)
    full = _proposal(st, diagnosis)
    assert full["outcome"] == "proposal"
    assert full["amount"] == {
        "value": 2,
        "ucum": "{tbl}",
    }  # a full dose fits again: the history here is empty
    assert full["lot_id"] == APAP_LOT  # the adverse inspection of partition A does not exclude the lot here


@pytest.mark.trace("T-57", "INV-M7")
@pytest.mark.parametrize("switch", ["mission", "mode"])
def test_records_of_another_mission_or_mode_are_invisible_to_dosing(tmp_path: Path, switch: str) -> None:
    st = build_station(tmp_path, mission=lunar("SIM"))
    diagnosis_a = _seed_partition_a(st)
    _assert_partition_a(st, diagnosis_a)

    st.mission.facts = _other_mission() if switch == "mission" else lunar("REPLAY")
    _assert_fresh_partition(st, diagnosis_a)
    proposal = _proposal(st, _diagnose(st))
    confirmed = _confirm(st, proposal)
    assert confirmed.status_code == 201, confirmed.text
    assert _lot(st, APAP_LOT)["quantity_now"] == {"value": 28, "ucum": "{tbl}"}  # this partition's own draw

    st.mission.facts = lunar(
        "SIM"
    )  # back to partition A: everything recorded there is still there, and only it
    _assert_partition_a(st, diagnosis_a)
    other = ("mission_2", "SIM") if switch == "mission" else ("mission_1", "REPLAY")
    assert sorted(st.rows("SELECT mission_id, origin_mode FROM med_movement")) == sorted(
        [("mission_1", "SIM"), other]
    )
