"""A served hub has no clock segment for a habitat adapter: only the hub's own segment, opened at start and
confirmed by the operator (S5.2). These tests drive that production path, not the station's hand-opened segments.

- The hub segment's capture revision is the operator's confirmation, so the hub's own receive stamp normalizes.
- A loopback adapter (E23 5.2, T0) reads the hub host's wall clock, so its SIM rows normalize on the hub segment and
  the channel is current; an adapter on another node stays unknown and fails closed.
- A REPLAY row keeps its dataset time (E23 3.1) whatever the adapter clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import Station, build_station, channel, reading

from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.uow import Actor

pytestmark = [pytest.mark.e23, pytest.mark.trace("T-E23-053", "T-E23-081", "D14")]
PATH = "/v1/adapters/adp_other/readings"
LOOPBACK, REMOTE = "http://127.0.0.1:9/health", "https://10.0.0.7:8443/health"
OPERATOR = Actor("operator", "operator_sim")


def _served_clocks(st: Station, source: str) -> PersistentClocks:
    return PersistentClocks(
        repository=SqlClockRepository(
            settings=st.db.settings, node_id=st.db.origin_node_id, incarnation_id=st.db.origin_incarnation
        ),
        settings=st.db.settings,
        ids=st.db.ids,
        hub_source_clock_id=source,
    )


def _hub_stamp(clocks: PersistentClocks, st: Station) -> Any:
    with st.db.snapshot() as view:
        source = clocks.hub_source_clock_id
        assert source is not None
        segment = clocks.for_sequence(view, source_clock_id=source, sequence=2**53 - 1)
        assert segment is not None
        stamp = TimeInput(
            view.now, source, segment.segment_id, segment.capture_offset_version, "hub_corrected", "SIM"
        )
        return clocks.normalize(view, stamp)


def test_the_hub_receive_stamp_normalizes_once_the_operator_confirms_the_segment(tmp_path: Path) -> None:
    st = build_station(tmp_path, build_app=False)
    clocks = _served_clocks(st, "hub_served")
    with st.db.begin(actor=Actor("engine", "engine_clock"), correlation_id="start") as uow:
        clocks.open_hub_segment(uow, process_epoch="epoch_served")
    assert _hub_stamp(clocks, st).state == "unknown"  # an unconfirmed hub clock proves nothing (fails closed)
    with st.db.begin(actor=OPERATOR, correlation_id="confirm") as uow:
        clocks.confirm_hub_time(uow, correction_id="cor_served", offset_ms=0, uncertainty_ms=0)
    st.advance(seconds=1)
    confirmed = _hub_stamp(clocks, st)
    assert (confirmed.state, confirmed.correction_id) == ("known", "cor_served")


def _fed(tmp: Path, health: str, origin: str, hub_uncertainty_ms: int = 0) -> tuple[Station, dict[str, Any]]:
    """One channel of an adapter that has no clock segment of its own, as every adapter of a served hub."""
    registry = {
        "channels": [
            channel(
                "ch_co2", "co2", "co2", origin, "ppm" if origin == "REPLAY" else "mmHg",
                adapter_id="adp_other", health_url=health, stale_after_seconds=300,
            )
        ]
    }  # fmt: skip
    st = build_station(tmp, registry=registry, hub_uncertainty_ms=hub_uncertainty_ms)
    st.initialize(origin)
    st.advance(seconds=60)
    if origin == "REPLAY":
        at = datetime(2014, 10, 7, 15, 36, tzinfo=UTC)  # an RR-1 dataset instant, long before activation
        row = reading(
            "ch_co2", 1, 4000.0, "ppm", at, "REPLAY", cabin_pressure_mmhg={"value": 760, "basis": "assumed"}
        )
    else:
        row = reading("ch_co2", 1, 2.0, "mmHg", st.clock.at - timedelta(seconds=1))
    reply = st.client.post(
        PATH, json={"batch_id": "b_1", "readings": [row]}, headers=st.headers("other_adapter", "k_1")
    )
    assert reply.status_code == 200 and reply.json()["rejected"] == [], reply.text
    found = st.client.get("/v1/sensor-channels", headers=st.headers("crew_a"))
    assert found.status_code == 200, found.text
    return st, next(c for c in found.json()["items"] if c["channel_id"] == "ch_co2")


def _time_state(st: Station) -> str:
    with st.db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT time_state FROM sensors_reading WHERE channel_id='ch_co2'", (), limit=2
        )
    return str(rows[0][0])


def test_a_loopback_adapter_reads_the_hub_clock_and_its_channel_is_current(tmp_path: Path) -> None:
    st, shown = _fed(tmp_path, LOOPBACK, "SIM")
    assert _time_state(st) == "known"
    assert shown["freshness"] == "current"


def test_an_adapter_on_another_node_without_a_segment_stays_unknown(tmp_path: Path) -> None:
    st, shown = _fed(tmp_path, REMOTE, "SIM")
    assert _time_state(st) == "unknown"
    assert shown["freshness"] == "stale"  # unproven time never shows current


def test_a_replay_row_keeps_its_dataset_time_and_freshness_follows_receive_time(tmp_path: Path) -> None:
    st, shown = _fed(tmp_path, REMOTE, "REPLAY")
    assert _time_state(st) == "replay"
    assert shown["freshness"] == "current"  # D14: the hub's receive reference, not the 2014 dataset date


def test_a_replay_row_is_current_on_a_hub_whose_confirmed_time_carries_uncertainty(tmp_path: Path) -> None:
    """The demo hub confirms its time to 500 ms. The receive reference and the freshness `now` are readings of that
    same clock, so its uncertainty cancels between them: a row received now is current, not stale for good."""
    st, shown = _fed(tmp_path, REMOTE, "REPLAY", hub_uncertainty_ms=500)
    assert _time_state(st) == "replay"
    assert shown["freshness"] == "current"
