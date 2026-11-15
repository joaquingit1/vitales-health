"""E28-owned V94/V95 behavior in E05 fallback.

V94's positive movement path needs E23 movement. V95/D40 positive handoff and
transport paths need E16 handoff consent and E29 delivery. Neither is claimed.
"""

import json
from pathlib import Path

import pytest
from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, StoredEvent
from stack import NOW, Station, checked, rows

pytestmark = pytest.mark.trace("V94", "V95", "D40", "T28-26", "T28-16", "EI5", "EI11")


@pytest.mark.parametrize("environment", ["lunar_surface", "mars_surface", "transit"])
def test_return_stage_depends_on_environment_without_inventing_movement(
    tmp_path: Path, environment: str
) -> None:
    """V94 and T28-26, the part E28 owns today.

    Mars and transit: no return stage and the configured no-return text (EI11). Lunar: no return stage is
    invented and the plan stays aboard. The lunar positive (sourced return content) is blocked: the shipped
    plan_eye_injury has no return stage because config/environment-profiles.json (E23) is not on main, and the
    package's own eye plan names supplies (eye_shield, eye_rinse_water, eye_wash_set) the shipped supply
    catalog (E32) lacks, so the release check rejects it.
    """
    station = Station(tmp_path)
    station.mission.environment = environment
    response = station.client.get("/v1/plans/plan_eye_injury")
    assert response.status_code == 200, response.text
    view = response.json()
    assert not any(stage["stage_id"] == "return" for stage in rows(view["stages"]))
    assert view["plan_aboard"] is True
    if environment == "lunar_surface":
        assert view["no_return_text"] is None
    else:
        assert view["no_return_text"]


def test_link_down_run_closes_and_episode_only_handoff_never_links(tmp_path: Path) -> None:
    station = Station(tmp_path)
    station.link.status = "down"
    run = station.start()
    event = StoredEvent(
        501,
        "event_handoff_unlinked",
        EventType.HANDOFF_CHANGED,
        NOW,
        NOW,
        EventScope("person", "mara", "lunar_surface"),
        "release_e28_shipped",
        "unlinked_handoff",
        json.dumps({"handoff_id": "hnd_unlinked", "episode_id": "ep_test", "state": "queued"}).encode(),
        Audience("handoffs.readers", "hnd_unlinked", AudienceProjectionKey("handoffs", "handoff", 1), "1"),
    )
    before = station.effects()
    with station.db.begin(actor=station.base.session.actor, correlation_id="unlinked_handoff") as uow:
        station.runtime.handoff_event(uow, event)
    assert station.effects() == before
    run = checked(station.act(run, "step_done", step="icl10_note_episodes"))
    run = checked(station.act(run, "close", disposition="other"))
    assert run["state"] == "closed"
    assert run["handoff_id"] is None
