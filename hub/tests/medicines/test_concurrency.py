"""ConfirmDose under real concurrency (E32 4.4, E32-T-54): two threads, each with its own test client, released
together by a barrier against one file-backed SQLite station. The confirm unit of work serializes on E01's writer;
exactly one terminal event, one administration and one lot movement come out."""

from __future__ import annotations

import threading
import warnings
from pathlib import Path
from typing import cast

import pytest
from httpx import Response

from .harness import Station, build_station, headers
from .test_flow import CLIENT_TIME, _context, _diagnose, _propose

with warnings.catch_warnings():  # starlette's own deprecation notice on importing its test client, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

JOIN_SECONDS = 30.0  # bound on every barrier wait and join; a confirm takes milliseconds


def _confirm_at_once(st: Station, proposal: dict[str, object], keys: tuple[str, str]) -> list[Response]:
    """Both confirms leave the barrier together. Each request carries its own correlation ID, so the only shared
    allocation left is inside the writer's unit of work."""
    body = {
        "lot_id": proposal["lot_id"],
        "overrides": [],
        "acknowledged_alert_ids": [],
        "client_time": CLIENT_TIME,
    }
    path = f"/v1/dose-proposals/{proposal['proposal_id']}/confirm"
    barrier = threading.Barrier(len(keys), timeout=JOIN_SECONDS)
    responses: list[Response] = []
    errors: list[BaseException] = []

    def confirm(key: str, n: int) -> None:
        client = TestClient(st.client.app)
        try:
            barrier.wait()
            answer = client.post(
                path,
                json=body,
                headers=headers(key=key, **{"If-Match": '"1"', "X-Correlation-Id": f"cor_t{n}"}),
            )
            responses.append(answer)
        except BaseException as error:  # reported to the test thread below, never swallowed
            errors.append(error)

    threads = [threading.Thread(target=confirm, args=(key, n)) for n, key in enumerate(keys)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_SECONDS)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    return responses


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


def _written(st: Station) -> tuple[object, ...]:
    return (
        st.rows("SELECT count(*) FROM med_record WHERE kind='administration'")[0][0],
        st.rows("SELECT count(*) FROM med_movement")[0][0],
        st.rows("SELECT count(*) FROM med_admin_ingredient")[0][0],
        st.rows("SELECT state FROM med_proposal_terminal"),
    )


@pytest.mark.trace("T-54", "INV-M4", "INV-M5")
def test_one_proposal_confirmed_by_two_threads_with_different_keys_gives_one_administration(
    st: Station,
) -> None:
    proposal = _propose(st, _diagnose(st))
    audit_before = cast(int, st.rows("SELECT count(*) FROM platform_audit")[0][0])
    responses = _confirm_at_once(st, proposal, ("key-thread-a", "key-thread-b"))
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    (refused,) = [r for r in responses if r.status_code == 409]
    assert refused.json()["code"] == "INVALID_TRANSITION"
    assert refused.json()["current_state"]["state"] == "confirmed"
    assert _written(st) == (1, 1, 1, [("confirmed",)])
    assert st.rows("SELECT count(*) FROM platform_audit") == [(audit_before + 1,)]  # INV-M5: one audit entry


@pytest.mark.trace("T-54", "INV-M4")
def test_the_same_key_and_body_from_two_threads_replays_one_administration(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    responses = _confirm_at_once(st, proposal, ("key-thread-same", "key-thread-same"))
    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    first, second = responses
    assert first.content == second.content  # the replay answers the stored result, byte for byte
    assert _written(st) == (1, 1, 1, [("confirmed",)])
    listed = st.client.get("/v1/medication-administrations?subject=person_a", headers=headers()).json()[
        "items"
    ]
    assert [x["administration_id"] for x in listed] == [first.json()["administration_id"]]
