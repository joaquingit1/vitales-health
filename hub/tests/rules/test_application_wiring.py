"""E24 wiring on a real station: the registered subscriptions, deadlines, clock corrections, recovery and activation.

Every event enters the way a producer's does: a bus payload published in a unit of work, or the stored outbox event
handed to the handler the module registered for it. Nothing calls an application function that the registry does not
name. Test documents are scratch copies of the shipped ones (D15): only a threshold or a policy the shipped release
leaves null is filled in, never a level or a raiser.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
import rules_harness as h
from e24_station import RECORD_FACTS, build, edited_documents, replaced

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.kernel.clock import ClockCorrected, ClockRegressed
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, EventScope, StoredEvent
from crew_health.mission.application.ports import MissionEventRecorded
from crew_health.rules.application import core
from crew_health.rules.application.ports import InsightWarningPort
from crew_health.rules.domain.inputs import EvidenceKey, InsightDraft, RecordFacts
from crew_health.rules.domain.vocabulary import Level, OriginMode

pytestmark = pytest.mark.E24

THRESHOLD_S = 600  # a scratch value for the test copy; the shipped release leaves it null (INV-E24-14)


def rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def warnings_of(st: base.Station, definition: str) -> list[tuple[Any, ...]]:
    return rows(
        st,
        "SELECT warning_id, state, revision, clear_reason, level FROM rules_warning "
        f"WHERE definition_id = '{definition}' ORDER BY raised_at",
    )


def with_scratch_documents(
    monkeypatch: pytest.MonkeyPatch, *, ground: bool = False, policy: bool = False
) -> None:
    def edit(views: dict[ConfigName, Any]) -> None:
        if ground:
            doc = json.loads(views[ConfigName.WARNING_RULES].raw)
            for d in doc["definitions"]:
                if d["id"] == "ground_silence":
                    d["condition"]["after_next_contact_seconds"] = THRESHOLD_S
            replaced(views, ConfigName.WARNING_RULES, doc)
        if policy:
            replaced(views, ConfigName.ASSISTANT_POLICY, h.example_value("assistant-policy"))

    edited_documents(monkeypatch, edit)


def station(tmp: Path) -> base.Station:
    st = build(tmp, registry={"channels": []})
    st.initialize("SIM")
    st.advance(seconds=3600)
    return st


def publish(st: base.Station, event: object) -> None:
    with st.begin() as uow:
        uow.publish(event)  # type: ignore[arg-type]


def storm(
    st: base.Station, event_id: str, kind: str = "space_weather_alert", *, ago: int = 0
) -> MissionEventRecorded:
    at = st.clock.at - timedelta(seconds=ago)
    return MissionEventRecorded(event_id, kind, kind, at, None, st.clock.at, "SIM", "station_leo", (), None)


def stored(kind: EventType, data: dict[str, object], *, at: datetime, seq: int = 1) -> StoredEvent:
    key = AudienceProjectionKey("groundlink", "ground_link", 1)
    return StoredEvent(
        seq, f"evt_{seq}", kind, at, at, EventScope("crew", None, "station_leo"), "release_e23", None,
        json.dumps(data).encode(), Audience("groundlink.link", "ground-link", key, "1"),
    )  # fmt: skip


def link(status: str, *, nxt: datetime | None, planned: bool = False) -> dict[str, object]:
    return {
        "status": status, "one_way_delay_s": None, "next_contact_at": _iso(nxt), "last_contact_at": None,
        "source": "measured", "environment_id": "station_leo", "delay_text": "Seconds", "delay_source_ids": [],
        "gap_kind": None, "gap_planned": planned,
    }  # fmt: skip


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%dT%H:%M:%SZ")


def deliver(st: base.Station, subscriber: str, event: StoredEvent) -> None:
    subscriptions = st.resolver().require(DurableSubscriptions)
    types, handler = subscriptions.handlers[subscriber]
    assert event.type in types, "the module registers this subscriber for this event type"
    with st.begin(Actor("engine", "engine_events")) as uow:
        handler(uow, event)


def fire_due(st: base.Station) -> int:
    return st.dispatch()


# --- ground silence (3.5, 3.11) -------------------------------------------------------------------


@pytest.mark.trace("T-E24-086")
def test_ground_silence_waits_for_its_deadline_raises_then_clears_when_the_link_is_back(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, ground=True)
    st = station(tmp_path)
    contact = st.clock.at + timedelta(seconds=120)
    deliver(
        st,
        "rules.ground_link",
        stored(EventType.GROUND_LINK_CHANGED, link("down", nxt=contact), at=st.clock.at),
    )
    assert warnings_of(st, "ground_silence") == []  # the contact is still ahead: only a deadline exists
    assert rows(st, "SELECT kind, due_at FROM rules_deadline WHERE deadline_key LIKE 'ground_silence:%'")
    st.advance(seconds=120 + THRESHOLD_S - 1)
    fire_due(st)
    assert warnings_of(st, "ground_silence") == []  # one second before contact plus the threshold
    st.advance(seconds=2)
    fire_due(st)
    [(wid, state, _, _, level)] = warnings_of(st, "ground_silence")
    assert (state, level) == ("raised", "watch")
    what = rows(st, f"SELECT body_json FROM rules_warning WHERE warning_id = '{wid}'")[0][0]
    assert "unknown" not in json.loads(what)["what"].lower()  # silence and contact time are both worded
    st.advance(seconds=5)  # the link must be observed after the raise to end it (D3)
    again = stored(EventType.GROUND_LINK_CHANGED, link("up", nxt=None), at=st.clock.at, seq=2)
    deliver(st, "rules.ground_link", again)
    assert warnings_of(st, "ground_silence")[0][1:4] == ("cleared", 2, "condition_false")


@pytest.mark.trace("T-E24-086", "T-E24-034")
def test_a_planned_gap_and_an_older_link_state_raise_nothing(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, ground=True)
    st = station(tmp_path)
    past = st.clock.at - timedelta(hours=2)
    deliver(
        st, "rules.ground_link",
        stored(EventType.GROUND_LINK_CHANGED, link("down", nxt=past, planned=True), at=st.clock.at),
    )  # fmt: skip
    st.advance(seconds=10)
    fire_due(st)
    assert warnings_of(st, "ground_silence") == []
    older = stored(
        EventType.GROUND_LINK_CHANGED, link("down", nxt=past), at=st.clock.at - timedelta(hours=1), seq=2
    )
    deliver(st, "rules.ground_link", older)  # delivered late: it does not replace the newer observation
    assert warnings_of(st, "ground_silence") == []


@pytest.mark.trace("T-E24-086")
def test_a_forward_clock_correction_may_raise_ground_silence_and_a_backward_one_never_clears_it(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, ground=True)
    st = station(tmp_path)
    contact = st.clock.at + timedelta(seconds=300)
    deliver(
        st,
        "rules.ground_link",
        stored(EventType.GROUND_LINK_CHANGED, link("down", nxt=contact), at=st.clock.at),
    )
    assert warnings_of(st, "ground_silence") == []
    forward = ClockCorrected(0, 2 * 3600 * 1000, st.clock.at, 2, "operator")
    st.advance(hours=2)  # the hub's reading of time jumps with the correction
    publish(st, forward)
    assert len(warnings_of(st, "ground_silence")) == 1  # re-evaluated at once, not at the next scheduler tick
    backward = ClockCorrected(2 * 3600 * 1000, 0, st.clock.at, 3, "operator")
    st.advance(hours=-2)
    publish(st, backward)
    assert (
        warnings_of(st, "ground_silence")[0][1] == "raised"
    )  # a correction is never evidence that silence ended


@pytest.mark.trace("T-E24-086")
def test_a_malformed_link_event_is_never_guessed_at_and_degrades_health(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, ground=True)
    st = station(tmp_path)
    bad = link("down", nxt=st.clock.at)
    bad["status"] = "sideways"
    deliver(st, "rules.ground_link", stored(EventType.GROUND_LINK_CHANGED, bad, at=st.clock.at))
    components = {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}
    assert components.get("rules") == core.FAULT_CODE
    assert warnings_of(st, "ground_silence") == []


# --- recovery after an evaluator fault (5.5, T-E24-122) -------------------------------------------


@pytest.mark.trace("T-E24-122", "T-E24-072")
def test_recovery_after_the_fault_is_fixed_raises_from_the_backlog_and_drains_the_row(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = station(tmp_path)
    original = core.engine.evaluate

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected evaluator fault")

    monkeypatch.setattr(core.engine, "evaluate", broken)
    when = st.clock.at - timedelta(seconds=90)
    publish(st, storm(st, "evt_storm_1", ago=90))
    assert warnings_of(st, "radiation_storm_shelter") == []  # nothing raised, the event is not lost
    assert rows(st, "SELECT state, attempts FROM rules_pending") == [("pending", 0)]
    health = {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}
    assert health["rules"] == core.FAULT_CODE
    monkeypatch.setattr(core.engine, "evaluate", original)  # the fix
    st.advance(seconds=30)
    fire_due(st)
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(0,)]
    [(wid, state, _, _, level)] = warnings_of(st, "radiation_storm_shelter")
    assert (state, level) == ("raised", "emergency")
    body = json.loads(rows(st, f"SELECT body_json FROM rules_warning WHERE warning_id = '{wid}'")[0][0])
    assert body["evidence_time"].startswith(when.strftime("%Y-%m-%dT%H:%M:%S"))  # original time, not recovery
    health = {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}
    assert health["rules"] is None  # healthy again: the component reports no code


@pytest.mark.trace("T-E24-122")
def test_a_row_that_keeps_failing_backs_off_and_sticks_and_an_activation_tries_it_again(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = station(tmp_path)
    original = core.engine.evaluate
    monkeypatch.setattr(core.engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    publish(st, storm(st, "evt_storm_2"))
    for _ in range(12):
        st.advance(hours=2)  # past each doubling of the back-off (1 s up to the hour)
        fire_due(st)
    [(state, attempts)] = rows(st, "SELECT state, attempts FROM rules_pending")
    assert (state, attempts) == ("stuck", 10)  # rules.reeval_max_attempts
    monkeypatch.setattr(core.engine, "evaluate", original)
    deliver(
        st, "rules.activation",
        stored(EventType.CONFIG_RELEASE_ACTIVATED, {}, at=st.clock.at, seq=5),
    )  # fmt: skip
    assert rows(st, "SELECT state, attempts FROM rules_pending") == [("pending", 0)]
    fire_due(st)
    assert rows(st, "SELECT count(*) FROM rules_pending") == [(0,)]
    assert len(warnings_of(st, "radiation_storm_shelter")) == 1


# --- activation (3.8, D1) ---------------------------------------------------------------------------


@pytest.mark.trace("T-E24-073", "T-E24-074", "T-E24-016")
@pytest.mark.parametrize(
    "kind",
    [EventType.CONFIG_RELEASE_ACTIVATED, EventType.ENVIRONMENT_ACTIVATED],
    ids=["release", "environment"],
)
def test_an_activation_never_clears_or_lowers_an_active_warning(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: EventType
) -> None:
    st = station(tmp_path)
    publish(st, storm(st, "evt_storm_3"))
    before = warnings_of(st, "radiation_storm_shelter")
    assert [r[1:3] for r in before] == [("raised", 1)]
    deliver(st, "rules.activation", stored(kind, {}, at=st.clock.at, seq=7))
    deliver(st, "rules.activation", stored(kind, {}, at=st.clock.at, seq=8))  # at least once: repeated
    assert warnings_of(st, "radiation_storm_shelter") == before


@pytest.mark.trace("T-E24-073")
def test_an_activation_re_evaluates_the_stored_link_state_under_the_new_release(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, ground=True)
    st = station(tmp_path)
    contact = st.clock.at - timedelta(hours=1)  # long past, and the link is down since
    deliver(
        st,
        "rules.ground_link",
        stored(EventType.GROUND_LINK_CHANGED, link("down", nxt=contact), at=st.clock.at),
    )
    [(wid, *_)] = warnings_of(st, "ground_silence")
    assert warnings_of(st, "ground_silence")[0][1] == "raised"
    deliver(st, "rules.activation", stored(EventType.CONFIG_RELEASE_ACTIVATED, {}, at=st.clock.at, seq=9))
    assert [r[0] for r in warnings_of(st, "ground_silence")] == [
        wid
    ]  # one active warning per key (INV-E24-05)


# --- the clock moves relative timers and not absolute deadlines (3.11, T-E24-084, 085, 088) --------


def accept_insight(st: base.Station, insight_id: str = "ins_w1") -> None:
    key = EvidenceKey("observation", "obs_w1")
    RECORD_FACTS.rows[key] = RecordFacts(
        True, "per_a", OriginMode.SIM, False, "ok", "accepted", st.clock.at, "pulse"
    )
    draft = InsightDraft(
        insight_id, "per_a", "sleep_reaction", Level.WATCH, "Short sleep", "You slept less.", "From your records.",
        "Short sleep", ("Go to bed earlier.",), "eyes", (key,), (), False, (), OriginMode.SIM, st.clock.at, "model_1",
    )  # fmt: skip
    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        raised = st.resolver().require(InsightWarningPort).validate_and_raise(uow, draft)
    assert raised is not None and raised.level == "watch" and raised.raised_by == "assistant"


def expiry_due(st: base.Station) -> datetime:
    [(due,)] = rows(st, "SELECT due_at FROM rules_deadline WHERE deadline_key LIKE 'expire:%'")
    return datetime.fromisoformat(due)


@pytest.mark.trace("T-E24-084", "T-E24-085")
def test_a_clock_correction_shifts_every_pending_expiry_by_the_correction(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, policy=True)
    st = station(tmp_path)
    accept_insight(st)
    first = expiry_due(st)
    assert first == st.clock.at + timedelta(seconds=86400)
    publish(
        st, ClockCorrected(0, 3_600_000, st.clock.at, 2, "operator")
    )  # the hub learns it was an hour behind
    assert expiry_due(st) == first + timedelta(hours=1)  # a forward jump never expires early
    publish(st, ClockCorrected(3_600_000, 1_800_000, st.clock.at, 3, "operator"))
    assert expiry_due(st) == first + timedelta(minutes=30)  # a backward jump never extends past the shift


@pytest.mark.trace("T-E24-088")
def test_expiry_is_suspended_while_the_clock_is_regressed_and_resumes_when_corrected(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, policy=True)
    st = station(tmp_path)
    accept_insight(st)
    publish(st, ClockRegressed(st.clock.at, st.clock.at + timedelta(seconds=5), 5000))
    st.advance(seconds=86400 + 10)
    fire_due(st)
    assert warnings_of(st, "assistant_pattern")[0][1] == "raised"  # expiry clears are suspended
    publish(st, ClockCorrected(0, 0, st.clock.at, 3, "operator"))
    st.advance(seconds=10)
    fire_due(st)
    assert warnings_of(st, "assistant_pattern")[0][1:4] == ("cleared", 2, "expired")


# --- insights through the port (4.2, UC-R5, UC-R6, V65, D17) ---------------------------------------


@pytest.mark.trace("T-E24-061", "T-E24-064")
def test_only_the_person_dismisses_an_insight_and_the_assistant_never_does(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, policy=True)
    st = station(tmp_path)
    accept_insight(st)
    port = st.resolver().require(InsightWarningPort)
    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        port.on_insight_dismissed(uow, "ins_w1", uow.actor)
    with st.begin(Actor("person", "per_b")) as uow:
        port.on_insight_dismissed(uow, "ins_w1", uow.actor)  # another person
    assert warnings_of(st, "assistant_pattern")[0][1] == "raised"
    with st.begin(Actor("person", "per_a")) as uow:
        port.on_insight_dismissed(uow, "ins_w1", uow.actor)
    assert warnings_of(st, "assistant_pattern")[0][1] == "dismissed"
    assert rows(
        st, "SELECT count(*) FROM rules_deadline WHERE deadline_key LIKE 'expire:%' AND due_at IS NOT NULL"
    ) == [(0,)]  # the expiry timer ended with the warning


@pytest.mark.trace("T-E24-060")
def test_an_insight_with_unverifiable_or_foreign_evidence_stores_nothing(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_scratch_documents(monkeypatch, policy=True)
    st = station(tmp_path)
    key = EvidenceKey("observation", "obs_x")
    RECORD_FACTS.rows[key] = RecordFacts(
        True, "per_b", OriginMode.SIM, False, "ok", "accepted", st.clock.at, "x"
    )
    draft = InsightDraft(
        "ins_bad", "per_a", "sleep_reaction", Level.WATCH, "t", "t", "w", "f", ("s",), "eyes", (key,), (), False, (),
        OriginMode.SIM, st.clock.at, "m",
    )  # fmt: skip
    port = st.resolver().require(InsightWarningPort)
    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        assert port.validate_and_raise(uow, draft) is None  # another person's record
    assert rows(st, "SELECT count(*) FROM rules_warning") == [(0,)]


@pytest.mark.trace("T-E24-060")
def test_without_the_records_owner_no_insight_can_be_verified_and_none_is_stored(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from crew_health.rules.application import insight as insight_module
    from crew_health.rules.application.core import Runtime

    with_scratch_documents(monkeypatch, policy=True)
    st = station(tmp_path)
    rt = Runtime(settings=st.db.settings, ids=st.db.ids, mission=None, binding=None, health=None)
    key = EvidenceKey("observation", "obs_y")
    draft = InsightDraft(
        "ins_none", "per_a", "sleep_reaction", Level.WATCH, "t", "t", "w", "f", ("s",), "eyes", (key,), (), False, (),
        OriginMode.SIM, st.clock.at, "m",
    )  # fmt: skip
    from crew_health.kernel.errors import ProblemError

    with st.begin(Actor("assistant", "assistant_stub")) as uow:
        with pytest.raises(ProblemError) as caught:
            insight_module.InsightPort(rt).validate_and_raise(uow, draft)
    assert caught.value.retryable
    assert rows(st, "SELECT count(*) FROM rules_warning") == [(0,)]
