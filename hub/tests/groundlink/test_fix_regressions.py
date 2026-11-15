from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import EventType
from crew_health.groundlink.application.ports import OutboxItem
from crew_health.help.application.ports import EventOutcome
from crew_health.kernel.uow import Actor

from .support import Harness, Lifecycle, event, hazards, release


class FailPattern(Lifecycle):
    def __init__(self, failures: list[int]) -> None:
        super().__init__()
        self.failures = dict(enumerate(failures))

    def mark_sent(
        self,
        message_id: str,
        event_id: str,
        sent_at: datetime,
        one_way_delay_s: float | None,
        simulated: bool,
    ) -> EventOutcome:
        index = int(message_id[4:])
        if self.failures.get(index, 0):
            self.failures[index] -= 1
            raise RuntimeError("temporary owner failure")
        return super().mark_sent(message_id, event_id, sent_at, one_way_delay_s, simulated)


def failing_owner(h: Harness, failures: list[int]) -> FailPattern:
    owner = FailPattern(failures)
    h.runtime.services.deps.lifecycle = owner
    return owner


@pytest.mark.trace("R61", "E29-04")
def test_owner_failure_keeps_later_bundle_unchanged_until_head_is_sent(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    owner = failing_owner(h, [1, 0])
    h.enqueue(2)
    assert h.runtime.agent.run_once().sent == 0
    assert h.status().state == "sending"
    assert h.status("msg_0001").state == "queued"
    assert h.status("msg_0001").attempts == 0
    with h.db.snapshot() as view:
        later = h.runtime.services.deps.repository.bundle(view, "ground_message", "msg_0001")
        assert later is not None and later.next_attempt_at is None
    assert h.runtime.agent.run_once().sent == 0
    assert owner.calls == []
    h.clock.at += timedelta(seconds=1)
    assert h.runtime.agent.run_once().sent == 2
    h.clock.at += timedelta(seconds=10)
    h.runtime.agent.run_once()
    assert [c[1] for c in owner.calls if c[0] == "sent"] == ["msg_0000", "msg_0001"]
    assert [c[1] for c in owner.calls if c[0] == "arrived"] == ["msg_0000", "msg_0001"]


@pytest.mark.trace("R61", "E29-04")
@settings(max_examples=20, deadline=None)
@given(st.lists(st.integers(0, 3), min_size=1, max_size=8))
def test_random_owner_failures_never_invert_sent_order(failures: list[int]) -> None:
    with TemporaryDirectory() as directory:
        h = Harness(Path(directory))
        owner = failing_owner(h, failures)
        h.enqueue(len(failures))
        for _ in range(sum(failures) + len(failures) + 1):
            h.runtime.agent.run_once()
            h.clock.at += timedelta(seconds=10)
        expected = [f"msg_{i:04}" for i in range(len(failures))]
        assert [c[1] for c in owner.calls if c[0] == "sent"] == expected
        stamps = [h.status(ref).sent_at for ref in expected]
        assert all(
            a is not None and b is not None and a <= b for a, b in zip(stamps, stamps[1:], strict=False)
        )


@pytest.mark.trace("R61", "E29-04")
def test_handoff_waits_in_the_same_queue_after_failed_ground_message(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    failing_owner(h, [1])
    h.enqueue()
    with h.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor_handoff") as uow:
        h.outbox.enqueue(
            uow, OutboxItem("handoff", "handoff_1", "b" * 64, h.clock.at + timedelta(seconds=1), True)
        )
    assert h.runtime.agent.run_once().sent == 0
    with h.db.snapshot() as view:
        item = h.outbox.status(view, "handoff", "handoff_1")
        assert item is not None and item.state == "queued" and item.attempts == 0


@pytest.mark.trace("R49", "D33", "E29-01")
def test_first_activation_records_and_emits_once_with_no_previous_history(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.mission.facts.active_environment_id = "lunar_surface"
    with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_sub") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "lunar_surface"})
        )
    h.runtime.agent.run_once()
    assert len([e for e in h.events if e.type == EventType.GROUND_LINK_CHANGED]) == 1
    with h.db.snapshot() as view:
        assert h.runtime.services.deps.repository.environment(view) == "lunar_surface"


@pytest.mark.trace("V93", "D35", "E29-03")
@pytest.mark.parametrize("planned", [True, False])
def test_only_planned_demo_gap_expires_at_contact(tmp_path: Path, planned: bool) -> None:
    h = Harness(tmp_path)
    contact = h.clock.at + timedelta(hours=26)
    response = h.client.post(
        "/v1/demo/ground-link",
        json={
            "status": "down",
            "gap_planned": planned,
            "gap_kind": "planned_line_of_sight",
            "next_contact_at": contact.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        headers={"Idempotency-Key": "command_0001"},
    )
    assert response.status_code == 200
    h.clock.at = contact
    h.runtime.agent.run_once()
    count = len(h.events)
    h.clock.at += timedelta(hours=2)
    assert h.runtime.agent.run_once().observed == 0
    with h.db.snapshot() as view:
        link = h.query.current(view)
        assert link is not None
        assert link.source == ("schedule" if planned else "demo_switch")
        assert (link.status == "down") is (not planned)
    assert count == (2 if planned else 1)


@pytest.mark.trace("R49", "E29-01")
@pytest.mark.parametrize("mode", ["LIVE", "REPLAY"])
def test_stored_demo_override_is_served_only_in_sim_or_replay(tmp_path: Path, mode: str) -> None:
    h = Harness(tmp_path)
    assert (
        h.client.post(
            "/v1/demo/ground-link", json={"status": "down"}, headers={"Idempotency-Key": "command_0001"}
        ).status_code
        == 200
    )
    h.mission.facts.mission_mode = cast(Literal["LIVE", "REPLAY", "SIM"], mode)
    got = h.client.get("/v1/ground-link", headers={"role": "crew"}).json()
    assert got["source"] == ("schedule" if mode == "LIVE" else "demo_switch")
    h.runtime.agent.run_once()
    with h.db.snapshot() as view:
        assert h.runtime.services.deps.repository.history(view)[-1].link.source == got["source"]
    h.mission.facts.mission_mode = "SIM"
    assert h.client.get("/v1/ground-link").json()["source"] == "demo_switch"
    with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_sub") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
        )
    assert h.client.get("/v1/ground-link").json()["source"] == "schedule"


@pytest.mark.trace("R49", "E29-01")
def test_absent_mission_port_ignores_stored_demo_override(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_sub") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
        )
    h.client.post(
        "/v1/demo/ground-link", json={"status": "down"}, headers={"Idempotency-Key": "command_0001"}
    )
    h.runtime.services.deps.mission = None
    assert h.client.get("/v1/ground-link").json()["source"] == "schedule"


@pytest.mark.trace("R48", "E29-01")
def test_delay_sources_use_hazards_catalog_and_preserve_unknown_groups(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    hazard = hazards()
    sources = cast(list[dict[str, object]], hazard["sources"])
    sources.append(dict(sources[0], id="EV99"))
    rows = cast(list[dict[str, object]], hazard["environments"])
    next(e for e in rows if e["id"] == "station_leo")["earth_delay"] = "Delay (EV99) (EV98) (EV99, EV98)."
    h.release = release(hazard=hazard)
    got = h.client.get("/v1/ground-link").json()
    assert got["delay_source_ids"] == ["EV99"]
    assert got["delay_text"] == "Delay (EV98) (EV99, EV98)."


@pytest.mark.trace("R48", "E29-01")
def test_source_parser_accepts_catalog_ids_with_new_prefixes() -> None:
    from crew_health.groundlink.domain.link import split_delay_text

    document = hazards()
    sources = cast(list[dict[str, object]], document["sources"])
    sources.extend([dict(sources[0], id="QX01"), dict(sources[0], id="PM01")])
    known_ids = {cast(str, source["id"]) for source in sources}
    assert split_delay_text("Delay (QX01, PM01) (ZZ99).", known_ids) == (
        "Delay (ZZ99).",
        ("QX01", "PM01"),
    )


@pytest.mark.trace("V93", "D35", "E29-03")
def test_late_observation_after_planned_demo_contact_emits_one_change(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    contact = h.clock.at + timedelta(hours=26)
    response = h.client.post(
        "/v1/demo/ground-link",
        json={
            "status": "down",
            "gap_planned": True,
            "next_contact_at": contact.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        headers={"Idempotency-Key": "command_0001"},
    )
    assert response.status_code == 200
    h.clock.at = contact + timedelta(hours=2)
    assert h.runtime.agent.run_once().observed == 1
    assert h.runtime.agent.run_once().observed == 0
    with h.db.snapshot() as view:
        link = h.query.current(view)
        assert link is not None and link.status == "up" and link.source == "schedule"
        assert link.next_contact_at is None
    assert len(h.events) == 2
