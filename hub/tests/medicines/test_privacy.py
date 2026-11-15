"""Object and field visibility before anything clinical leaves the hub (review B4 and B5; SYSTEM 8.3, 5.8; AGENTS 6
Privacy). A read needs the subject or the subject's consent for that field; officer status, an acting assignment or
a help session is never a chart grant. A confirm error that carries the proposal is returned only to a reader, and a
saved result is replayed only under the current grant."""

from __future__ import annotations

from typing import cast

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import Audience
from crew_health.medicines.application import events

from .harness import CMO, CREW_B, SESSIONS, SUBJECT, Station, build_station, headers
from .test_flow import CLIENT_TIME as TIME
from .test_flow import _confirm, _context, _diagnose, _key, _post, _propose


@pytest.fixture
def st(tmp_path_factory: pytest.TempPathFactory) -> Station:
    station = build_station(tmp_path_factory.mktemp("privacy"))
    _context(station)
    return station


def _body(proposal: dict[str, object]) -> dict[str, object]:
    return {"lot_id": proposal["lot_id"], "overrides": [], "acknowledged_alert_ids": [], "client_time": TIME}


def _unshared(st: Station) -> None:
    st.consent.grants.clear()  # nobody shared anything: only the subject reads


@pytest.mark.trace("REVIEW-B4", "SYSTEM-8.3", "AGENTS-6")
def test_a_confirm_error_never_discloses_the_proposal_to_someone_without_a_grant(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    stale = _post(
        st,
        f"/v1/dose-proposals/{proposal['proposal_id']}/confirm",
        _body(proposal),
        "crew_b",
        **{"If-Match": '"9"'},
    )
    assert stale.status_code == 403 and stale.json()["code"] == "UNAUTHORIZED_SCOPE"
    assert stale.json()["current_state"] is None and SUBJECT not in stale.text
    assert (
        _confirm(st, proposal).status_code == 201
    )  # now terminal: an error would carry it regardless of revision
    terminal = _confirm(st, proposal, token="crew_b")
    assert terminal.status_code == 403 and terminal.json()["current_state"] is None
    assert "acetaminophen" not in terminal.text and SUBJECT not in terminal.text


@pytest.mark.trace("REVIEW-B5", "SYSTEM-8.3", "PRD-146")
def test_officer_status_alone_reads_nothing(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    administration = _confirm(st, proposal).json()
    _unshared(st)
    for path in (
        f"/v1/people/{SUBJECT}/medication-context",
        f"/v1/dose-proposals/{proposal['proposal_id']}",
        f"/v1/medication-administrations?subject={SUBJECT}",
    ):
        response = st.client.get(path, headers=headers("cmo"))
        assert response.status_code == 403 and response.json()["code"] == "UNAUTHORIZED_SCOPE", path
        assert st.client.get(path, headers=headers("subject")).status_code == 200, path
    terminal = _confirm(st, proposal)
    assert terminal.status_code == 403 and terminal.json()["current_state"] is None
    corrected = _post(
        st,
        f"/v1/medication-administrations/{administration['administration_id']}/corrections",
        {"reason_text": "wrong subject", "client_time": TIME},
    )
    assert corrected.status_code == 403 and corrected.json()["current_state"] is None
    created = _post(
        st,
        "/v1/dose-proposals",
        {"working_diagnosis_id": _diagnose(st), "product_id": "acetaminophen_325_tab", "client_time": TIME},
    )
    assert created.status_code == 403 and created.json()["code"] == "UNAUTHORIZED_SCOPE"


@pytest.mark.trace("REVIEW-B5", "SYSTEM-8.3")
def test_a_field_grant_covers_only_its_field(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    _unshared(st)
    st.consent.grants.add((SUBJECT, CREW_B, "proposals"))
    assert (
        st.client.get(f"/v1/dose-proposals/{proposal['proposal_id']}", headers=headers("crew_b")).status_code
        == 200
    )
    context = st.client.get(f"/v1/people/{SUBJECT}/medication-context", headers=headers("crew_b"))
    assert context.status_code == 403
    listed = st.client.get(f"/v1/medication-administrations?subject={SUBJECT}", headers=headers("crew_b"))
    assert listed.status_code == 403


@pytest.mark.trace("REVIEW-B5", "SYSTEM-5.8")
def test_a_replay_returns_the_saved_result_only_under_the_current_grant(st: Station) -> None:
    st.consent.grants.add(("*", CREW_B, "*"))
    st.assign_acting(CREW_B, SUBJECT)
    proposal = _propose(st, _diagnose(st))
    key = _key()
    first = _confirm(st, proposal, token="crew_b", key=key)
    assert first.status_code == 201, first.text
    assert _confirm(st, proposal, token="crew_b", key=key).json() == first.json()  # replay under the grant
    st.authority.acting.clear()
    st.consent.grants.discard(("*", CREW_B, "*"))  # the assignment ended and the share was revoked
    replay = _confirm(st, proposal, token="crew_b", key=key)
    assert replay.status_code == 403 and replay.json()["code"] == "UNAUTHORIZED_SCOPE"
    assert first.json()["administration_id"] not in replay.text and replay.json()["current_state"] is None


@pytest.mark.trace("REVIEW-B5", "SYSTEM-8.3", "E32-4.5", "R-E32-11")
def test_person_events_reach_the_subject_and_consented_readers_only(st: Station) -> None:
    administered = Audience(
        events.PERSON_RULE, SUBJECT, events.projection(EventType.MEDICATION_ADMINISTERED), "1"
    )
    proposed = Audience(
        events.PERSON_RULE, SUBJECT, events.projection(EventType.MEDICINE_PROPOSAL_CHANGED), "1"
    )
    with st.db.snapshot() as view:
        unbound = events.PersonAudience()
        assert unbound.admits(view, SESSIONS["subject"], administered)
        assert not unbound.admits(view, SESSIONS["cmo"], administered)  # officer status is not a grant
        rule = events.PersonAudience(lambda: st.consent)
        st.consent.grants.clear()
        st.consent.grants.add((SUBJECT, CMO, "administrations"))
        assert rule.admits(view, SESSIONS["cmo"], administered)
        assert not rule.admits(view, SESSIONS["cmo"], proposed)  # another field
        assert not rule.admits(view, SESSIONS["crew_b"], administered)


@pytest.mark.trace("REVIEW-GAPS", "SYSTEM-5.8")
def test_if_match_is_part_of_the_confirmation_identity(st: Station) -> None:
    """SYSTEM 5.8: every semantically relevant header is a parameter. The same key with another If-Match is a
    conflict, never a replay of the first answer."""
    proposal = _propose(st, _diagnose(st))
    key = _key()
    assert _confirm(st, proposal, key=key).status_code == 201
    other = _post(
        st,
        f"/v1/dose-proposals/{proposal['proposal_id']}/confirm",
        _body(proposal),
        "cmo",
        key,
        **{"If-Match": '"2"'},
    )
    assert other.status_code in {409, 422}
    assert cast(str, other.json()["code"]) in {"DUPLICATE_COMMAND", "IDENTITY_MISMATCH"}
