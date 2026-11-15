"""V93 and D35 from groundlink's side: what the E24 ground_silence evaluator reads.

The evaluator itself belongs to the rules module (E24, docs/tdd/E24.md line 171), which is not on main. The
predicate below is a structural copy of that row, kept in the test so the GroundLinkQuery this module provides is
proved sufficient for it: raise when the link is down, next_contact_at is set, the threshold is not null and the
clock is past next_contact_at plus the threshold. A planned gap raises nothing (CONTRACTS 26, V93, D35), so the copy
also reads gap_planned. A null threshold raises nothing and is never replaced by a number in code
(docs/tdd/E24.md line 174); the shipped rule keeps it null until clinical review sets it.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.application.ports import GroundLinkView

from .support import ROOT, Harness

MOON_GAP_START = datetime(2026, 11, 20, tzinfo=UTC)
MOON_GAP_END = datetime(2026, 12, 3, 15, 51, 35, tzinfo=UTC)
FIXTURE_THRESHOLD_S = 3600.0
"""Test fixture only. No source sets this threshold (docs/product/USE-CASES.md line 240: clinical review)."""


def raises(link: GroundLinkView | None, threshold_s: float | None, now: datetime) -> bool:
    if link is None or threshold_s is None or link.status != "down" or link.next_contact_at is None:
        return False
    if link.gap_planned:
        return False
    return now > link.next_contact_at + timedelta(seconds=threshold_s)


def shipped_threshold() -> object:
    document = json.loads((ROOT / "contracts/api/examples/config/warning-rules.example.json").read_bytes())
    rule = next(d for d in document["value"]["definitions"] if d["id"] == "ground_silence")
    return rule["condition"]["after_next_contact_seconds"]


def config_profiles() -> dict[str, object]:
    data: dict[str, object] = json.loads((ROOT / "config/environment-profiles.json").read_bytes())
    return data


@pytest.mark.trace("V93", "D35", "R49", "E29-03")
@pytest.mark.parametrize("threshold_s", [None, 0.0, FIXTURE_THRESHOLD_S])
def test_planned_moon_gap_from_the_configured_schedule_raises_nothing(
    tmp_path: Path, threshold_s: float | None
) -> None:
    h = Harness(tmp_path, environment="lunar_surface", data=config_profiles())
    for at in (MOON_GAP_START + timedelta(seconds=1), MOON_GAP_END - timedelta(seconds=1)):
        h.clock.at = at
        with h.db.snapshot() as view:
            link = h.query.current(view)
        assert link is not None and link.status == "down"
        assert link.gap_planned is True and link.gap_kind == "planned_line_of_sight"
        assert link.next_contact_at == MOON_GAP_END
        assert not raises(link, threshold_s, at)


@pytest.mark.trace("V93", "R49", "E29-03")
def test_silence_past_next_contact_plus_threshold_raises_and_the_link_coming_back_clears(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    contact = h.clock.at + timedelta(hours=1)
    body = {"status": "down", "gap_planned": False, "next_contact_at": contact.strftime("%Y-%m-%dT%H:%M:%SZ")}
    response = h.client.post("/v1/demo/ground-link", json=body, headers={"Idempotency-Key": "command_0001"})
    assert response.status_code == 200
    assert [e.type for e in h.events] == [EventType.GROUND_LINK_CHANGED]
    deadline = contact + timedelta(seconds=FIXTURE_THRESHOLD_S)
    for at, expected in (
        (contact, False),
        (deadline - timedelta(seconds=1), False),
        (deadline + timedelta(seconds=1), True),
    ):
        h.clock.at = at
        with h.db.snapshot() as view:
            link = h.query.current(view)
        assert link is not None and link.status == "down" and link.next_contact_at == contact
        assert raises(link, FIXTURE_THRESHOLD_S, at) is expected
        assert not raises(link, None, at)
    back = h.client.post(
        "/v1/demo/ground-link", json={"status": "up"}, headers={"Idempotency-Key": "command_0002"}
    )
    assert back.status_code == 200 and len(h.events) == 2
    with h.db.snapshot() as view:
        assert not raises(h.query.current(view), FIXTURE_THRESHOLD_S, h.clock.at)


@pytest.mark.trace("V93", "E29-03")
def test_the_shipped_rule_keeps_the_threshold_null_until_clinical_review() -> None:
    assert shipped_threshold() is None
    ui_copy = json.loads((ROOT / "config/ui-copy.json").read_bytes())
    assert ui_copy["designed_states"]["ground_silence"] == "Ground out of contact longer than planned"


@pytest.mark.trace("V93", "D35", "E29-03")
def test_a_planned_demo_gap_ends_at_its_contact_and_never_reads_as_silence(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    contact = h.clock.at + timedelta(hours=26)
    body = {
        "status": "down",
        "gap_planned": True,
        "gap_kind": "planned_line_of_sight",
        "next_contact_at": contact.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert (
        h.client.post(
            "/v1/demo/ground-link", json=body, headers={"Idempotency-Key": "command_0001"}
        ).status_code
        == 200
    )
    h.clock.at = contact - timedelta(seconds=1)
    with h.db.snapshot() as view:
        during = h.query.current(view)
    assert during is not None and during.status == "down" and during.gap_planned is True
    assert not raises(during, 0.0, contact + timedelta(hours=2))
    # Reported as unplanned, the same stale facts would raise: the copy really reads gap_planned.
    assert raises(replace(during, gap_planned=False), 0.0, contact + timedelta(hours=2))
    h.clock.at = contact + timedelta(hours=2)
    h.runtime.agent.run_once()
    with h.db.snapshot() as view:
        after = h.query.current(view)
    assert after is not None and after.source == "schedule" and after.status != "down"
    assert [e.type for e in h.events].count(EventType.GROUND_LINK_CHANGED) >= 2
