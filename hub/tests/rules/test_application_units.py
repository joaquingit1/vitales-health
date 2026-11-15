"""Fast unit tests of E24's application layer without a station: codecs, stores over real SQLite, release facts,
the wire mapper and the loader check. Inputs come from the configuration through the harness (AGENTS 4)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import rules_harness as h

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.storage import StatementResult
from crew_health.kernel.testing import MemoryRelease
from crew_health.rules.adapters.config_reader import WarningRulesCheck
from crew_health.rules.application import codec, facts, input_codec
from crew_health.rules.application.stores import (
    ActionResultStore,
    DeadlineStore,
    DwellStore,
    FactStore,
    MissionEventStore,
    OccurrenceStore,
    PendingStore,
    ReplicaStore,
    WarningStore,
)
from crew_health.rules.application.wire import wire_json
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.dwell import DwellMarker
from crew_health.rules.domain.vocabulary import ConditionKind

pytestmark = pytest.mark.E24
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


class Session:
    """A StorageSession over real SQLite with the schema of the shipped migration."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        sql = (
            Path(__file__).resolve().parents[2] / "src/crew_health/rules/adapters/migrations/0001_rules.sql"
        ).read_text(encoding="utf-8")
        self.db.executescript(sql)

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> StatementResult:
        cursor = self.db.execute(sql, parameters)  # type: ignore[arg-type]
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(self, sql: str, parameters: tuple[object, ...], *, limit: int) -> tuple[tuple[object, ...], ...]:
        return tuple(self.db.execute(sql, parameters).fetchmany(limit))  # type: ignore[arg-type]


def raised(sensor_reading: str = "rdg_1"):  # type: ignore[no-untyped-def]
    w = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    w.feed(
        w.sensor(reading=sensor_reading, results=[h.result("co2_1h_act", crossed=True, clear_holds=False)])
    )
    return w, next(iter(w.warnings.values()))


def test_codecs_round_trip_every_input_kind() -> None:
    w, warning = raised()
    assert codec.loads(codec.dumps(warning)) == warning
    inputs = (
        w.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]),
        w.freshness_input("stale", since=w.now),
        w.event("e1", "space_weather_alert", w.now, persons=("per_a",)),
        h.insight_input(),
    )
    for inp in inputs:
        kind, text = input_codec.dumps(inp)
        assert input_codec.loads(kind, text) == inp
    with pytest.raises(Exception, match="unknown"):
        input_codec.loads("Evil", "{}")


def test_warning_store_unique_active_immutable_and_spent() -> None:
    c = Session()
    store = WarningStore()
    _, warning = raised()
    store.insert(c, warning)
    assert store.get(c, warning.warning_id) == warning
    assert store.active(c, limit=10) == (warning,)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert(
            c, __import__("dataclasses").replace(warning, warning_id="wrn_dup")
        )  # one active per key
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE rules_warning SET level = 'info' WHERE warning_id = ?", (warning.warning_id,))
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM rules_warning")
    assert store.page(c, state="all", after=None, limit=5) == (warning,)
    assert store.spent(c, ConditionKind.SENSOR_LIMIT, ["rdg_1"]) == frozenset()
    assert store.update(c, __import__("dataclasses").replace(warning, revision=2), expected_revision=1)
    assert not store.update(
        c, __import__("dataclasses").replace(warning, revision=2), expected_revision=1
    )  # moved
    with pytest.raises(Exception, match="one revision"):
        store.update(c, warning, expected_revision=5)


def test_small_stores() -> None:
    c = Session()
    _, warning = raised()
    WarningStore().insert(c, warning)
    occ = OccurrenceStore()
    args = dict(definition_id="d", scope_key="k", trigger_ref="e1", warning_id=warning.warning_id, kind="trigger",
                occurred_at=T0, recorded_at=T0)  # fmt: skip
    assert occ.record(c, **args) and not occ.record(c, **args)  # type: ignore[arg-type]
    assert len(occ.for_warnings(c, [warning.warning_id], limit=5)[warning.warning_id]) == 1
    assert occ.resolve_before(c, warning.warning_id, T0 + timedelta(seconds=1), "clr") == 1
    occ.record(c, **{**args, "trigger_ref": "c1", "kind": "clear", "warning_id": None})  # type: ignore[arg-type]
    assert occ.latest_clears(c, limit=5) == {("d", "k"): T0}
    dwell = DwellStore()
    marker = DwellMarker(warning.warning_id, "ch", "fp", T0, T0, "seg")
    dwell.put(c, marker)
    assert dwell.all(c, limit=5) == {warning.warning_id: marker}
    assert dwell.delete_not_in(c, (), limit=5) == 1
    dwell.put(c, marker)
    dwell.delete_for_channel(c, "ch")
    assert dwell.all(c, limit=5) == {}
    d = DeadlineStore()
    d.put(c, "k", "absolute", T0, None, T0, 3)
    assert d.get(c, "k") == ("absolute", T0, None, T0, 3) and d.page(c, after="", limit=5) == ("k",)
    d.delete(c, "k")
    assert d.get(c, "k") == ("absolute", None, None, T0, 3)  # the generation survives the fire
    f = FactStore()
    assert f.put(c, "g", T0, {"a": 1}) and not f.put(
        c, "g", T0, {"a": 2}
    )  # an older delivery never overwrites
    assert f.get(c, "g") == {"a": 1} and f.get(c, "none") is None
    results = ActionResultStore()
    results.put(c, "per_a", "k1", warning.warning_id, "{}", T0)
    assert results.get(c, "per_a", "k1") == (warning.warning_id, "{}", T0)
    replica = ReplicaStore()
    one = dict(warning_id="w", origin_node_id="n2", origin_hlc="h", revision=2, scope="crew", person_id=None,
               warning_json="{}", now=T0)  # fmt: skip
    assert replica.apply(c, **one) and not replica.apply(c, **{**one, "revision": 1})  # type: ignore[arg-type]
    assert len(replica.page(c, limit=5)) == 1
    mission = MissionEventStore()
    w = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    event = w.event("e9", "space_weather_alert", T0, persons=("per_a",), location="hab")
    mission.add(c, event)
    assert mission.latest(c, ["space_weather_alert"], per_type=3) == (event,)


def test_pending_store_coalesces_overflows_and_quarantines() -> None:
    c = Session()
    p = PendingStore()
    base = dict(kind="K", ref="K", input_json="{}", code="X", now=T0, max_rows=2)
    p.record(c, pending_id="p1", coalesce_key="a", **base)  # type: ignore[arg-type]
    p.record(c, pending_id="p2", coalesce_key="a", **base)  # type: ignore[arg-type]  # coalesces with p1
    assert p.count(c) == 1 and p.blocking(c)
    p.record(c, pending_id="p3", coalesce_key=None, **base)  # type: ignore[arg-type]
    p.record(c, pending_id="p4", coalesce_key=None, **base)  # type: ignore[arg-type]  # past the bound
    assert {r[0] for r in c.rows("SELECT state FROM rules_pending", (), limit=9)} >= {"overflow"}
    due = p.due(c, T0, 10)
    assert [r[0] for r in due][0] == "p2"
    p.failed(c, "p2", "Y", T0, stuck=True)
    assert p.quarantine(c, "p2") and not p.quarantine(c, "p2")
    p.done(c, "p2")
    assert p.next_seq(c) > 1


def test_release_facts_wire_and_loader_check() -> None:
    docs = {
        ConfigName.WARNING_RULES: h.deployment_rules(),
        ConfigName.SENSOR_LIMITS: h.example_value("sensor-limits"),
        ConfigName.SENSOR_CATALOG: h.example_value("sensor-catalog"),
        ConfigName.QUESTIONNAIRES: h.example_value("questionnaires"),
        ConfigName.ASSISTANT_POLICY: h.example_value("assistant-policy"),
        ConfigName.ENVIRONMENT_HAZARDS: h.hazards_document(),
        ConfigName.UI_COPY: {"origin_words": {"LIVE": "Live"}, "designed_states": {}},
        ConfigName.ROUTINE_RULES: h.example_value("routine-rules"),
    }
    raw = {n: json.dumps(v).encode() for n, v in docs.items()}
    views = {n: ConfigDocumentView(n, hashlib.sha256(b).hexdigest(), b) for n, b in raw.items()}
    release = MemoryRelease("rel_x", views)
    built = facts.build(release)
    assert "co2_1h_act" in built.limits and built.suppressed_overlays == {"sleep", "eva"}
    assert built.policy.allowed_warning_levels
    assert WarningRulesCheck(500).check(CandidateRelease("rel_x", views)) == ()
    assert WarningRulesCheck(1).check(CandidateRelease("rel_x", views))  # the definition bound rejects
    assert WarningRulesCheck(500).check(CandidateRelease("rel_x", {}))  # a missing document is a finding
    broken = dict(views)
    del broken[ConfigName.WARNING_RULES]
    with pytest.raises(Exception, match="unavailable"):
        facts.build(MemoryRelease("rel_y", broken))
    _, warning = raised("rdg_abc")
    wire = wire_json(warning, steps=warning.steps, allowed=())
    assert wire["evidence"][0]["summary"] == "rdg_abc" and wire["allowed_actions"] == []  # type: ignore[index]


# --- stored data that is not what was written is refused, never repaired (AGENTS 6: integrity errors fail loud) --


@pytest.mark.trace("INV-E24-11")
@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(warning_id=7),  # text expected
        lambda r: r.update(revision="1"),  # integer expected
        lambda r: r.update(revision=True),  # a bool is not an integer
        lambda r: r.update(steps="not a list"),
        lambda r: r.update(citation=["not", "an", "object"]),
        lambda r: r.update(raised_at=5),  # an instant is text
        lambda r: r.update(raised_at="2026-11-14T09:00:00"),  # and carries its offset
        lambda r: r.update(level="severe"),  # an enum is the whole enum
        lambda r: r.update(source_revision="x"),
    ],
    ids=["text", "integer", "bool", "list", "object", "instant_type", "instant_zone", "enum", "optional_int"],
)
def test_a_corrupt_stored_warning_is_refused(mutate) -> None:  # type: ignore[no-untyped-def]
    _, warning = raised()
    stored = codec.to_raw(warning)
    mutate(stored)
    with pytest.raises(Exception) as caught:  # noqa: PT011 - the type is part of the corruption under test
        codec.from_raw(stored)
    assert not isinstance(caught.value, KeyError)  # a typed refusal, not an accident of lookup


@pytest.mark.trace("INV-E24-11")
def test_a_corrupt_pending_input_is_refused_and_an_unsupported_one_is_never_written() -> None:
    w, _ = raised()
    kind, text = input_codec.dumps(w.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    body = json.loads(text)
    for bad in (
        {**body, "results": "nope"},  # a list expected
        {**body, "channel": ["nope"]},  # an object expected
        {**body, "origin_mode": "ELSEWHERE"},  # the whole enum
    ):
        with pytest.raises(Exception):  # noqa: B017, PT011
            input_codec.loads(kind, json.dumps(bad))
    with pytest.raises(Exception, match="unsupported"):
        input_codec.dumps(object())  # type: ignore[arg-type]
    deep: object = 1
    for _ in range(20):
        deep = (deep,)
    with pytest.raises(Exception, match="bound"):
        input_codec.dumps(deep)  # type: ignore[arg-type]


@pytest.mark.trace("INV-E24-14")
def test_the_loader_check_names_a_missing_or_unreadable_document_and_never_passes_it() -> None:
    docs = {
        ConfigName.WARNING_RULES: h.deployment_rules(),
        ConfigName.SENSOR_LIMITS: h.example_value("sensor-limits"),
        ConfigName.ENVIRONMENT_HAZARDS: h.hazards_document(),
        ConfigName.ASSISTANT_POLICY: h.example_value("assistant-policy"),
    }
    raw = {n: json.dumps(v).encode() for n, v in docs.items()}
    views = {n: ConfigDocumentView(n, hashlib.sha256(b).hexdigest(), b) for n, b in raw.items()}
    assert WarningRulesCheck(500).check(CandidateRelease("rel_ok", views)) == ()
    for name in (ConfigName.SENSOR_LIMITS, ConfigName.ENVIRONMENT_HAZARDS, ConfigName.ASSISTANT_POLICY):
        without = {k: v for k, v in views.items() if k is not name}
        assert WarningRulesCheck(500).check(CandidateRelease("rel_x", without)), name
    garbage = dict(views)
    garbage[ConfigName.WARNING_RULES] = ConfigDocumentView(ConfigName.WARNING_RULES, "x", b"{not json")
    assert WarningRulesCheck(500).check(CandidateRelease("rel_g", garbage))
    array = dict(views)
    array[ConfigName.WARNING_RULES] = ConfigDocumentView(ConfigName.WARNING_RULES, "x", b"[]")
    assert WarningRulesCheck(500).check(CandidateRelease("rel_a", array))
