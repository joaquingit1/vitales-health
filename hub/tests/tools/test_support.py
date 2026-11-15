from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.tools.domain.support import support_uses


@pytest.mark.trace("T-E12-020", "T-E12-021", "I3")
@pytest.mark.parametrize("presence", ["present", "absent", "unknown", "declined"])
def test_answer_support_preserves_all_presence_states(presence: str) -> None:
    from dataclasses import replace

    from tests.tools.test_context_builder import observation

    item = replace(observation(), kind="answer", presence=presence, quantity=None)
    expected = (
        frozenset({"finding", "hypothesis", "gap_closure"})
        if presence in {"present", "absent"}
        else frozenset()
    )
    assert item.permitted_uses(mission_origin="LIVE") == expected


@pytest.mark.trace("T-E12-020", "T-E12-021", "I3")
def test_absent_quantity_and_place_facts_never_gain_personal_support() -> None:
    from dataclasses import replace

    from tests.tools.test_context_builder import observation
    from tests.tools.test_gateway import View

    item = replace(observation(), quantity=None, observed_at=None, corrected_at=None)
    assert not item.permitted_uses(mission_origin="LIVE")
    item = replace(observation(), quantity=None)
    assert not item.permitted_uses(mission_origin="LIVE")
    item = replace(observation(), kind="reading", person_id="person_a")
    from crew_health.tools.domain.errors import ToolsInvalid

    with pytest.raises(ToolsInvalid):
        item.for_context(person_id="person_a", origin_mode="LIVE", at=View.now)


@pytest.mark.trace("T-E12-020", "T-E12-021", "T-E12-022", "I3")
@given(
    st.sampled_from(["accepted", "rejected", "uncertain", "not_assessed"]),
    st.sampled_from(["LIVE", "REPLAY", "SIM"]),
    st.sampled_from(["LIVE", "REPLAY", "SIM"]),
    st.sampled_from(["current", "stale", "unknown", "future"]),
    st.booleans(),
)
def test_support_requires_accepted_current_authorized_same_partition(
    quality: str, origin: str, mission_origin: str, freshness: str, authorized: bool
) -> None:
    uses = support_uses(
        quality=quality,
        origin=origin,
        mission_origin=mission_origin,
        freshness=freshness,
        method="manual_instrument",
        kind="observation",
        authorized=authorized,
    )
    expected = (
        quality == "accepted"
        and origin == mission_origin
        and origin != "REPLAY"
        and freshness == "current"
        and authorized
    )
    assert (uses == frozenset({"finding", "hypothesis", "gap_closure"})) == expected
    assert bool(uses) == expected


@pytest.mark.trace("T-E12-021", "T-E12-022", "V10", "I3")
@pytest.mark.parametrize(
    ("method", "kind", "expected"),
    [
        ("crewmate_palpation", "observation", frozenset({"finding"})),
        ("camera_estimate", "observation", frozenset({"finding", "hypothesis", "gap_closure"})),
        ("device", "sensor_reading", frozenset()),
        ("manual_instrument", "journal", frozenset()),
    ],
)
def test_place_and_private_records_never_support_person(
    method: str, kind: str, expected: frozenset[str]
) -> None:
    assert (
        support_uses(
            quality="accepted",
            origin="LIVE",
            mission_origin="LIVE",
            freshness="current",
            method=method,
            kind=kind,
            authorized=True,
        )
        == expected
    )


@pytest.mark.trace("T-E12-020", "I3")
def test_unknown_acquisition_method_never_becomes_support() -> None:
    assert (
        support_uses(
            quality="accepted",
            origin="LIVE",
            mission_origin="LIVE",
            freshness="current",
            method="unbound",
            kind="observation",
            authorized=True,
        )
        == frozenset()
    )
