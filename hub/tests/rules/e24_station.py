"""A real E24 station for integration tests: the E23 station (real E01 mechanisms, mission and sensors over a
temporary on-disk SQLite) plus the real rules module. Only what E01 has no production provider for is a named fake
in rules_station (session source, unit registry, roster). Configuration is the shipped config/ documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from crew_health.contracts_gen.enums import ConfigName  # noqa: E402
from crew_health.kernel.config import ConfigDocumentView  # noqa: E402
from crew_health.rules import module as rules_module  # noqa: E402
from crew_health.rules.application.ports import RecordFactsPort  # noqa: E402
from crew_health.rules.domain.inputs import EvidenceKey, RecordFacts  # noqa: E402
from crew_health.rules.settings import DEFAULTS as RULE_DEFAULTS  # noqa: E402
from crew_health.rules.settings import specifications as rule_specs  # noqa: E402

from . import rules_station as base

ADAPTER = base.ADAPTER
_SENSORS = base.sensors_module


class FakeRecordFacts:
    """The records owner has no `RecordFactsPort` yet (R-E24-14): the named fake of the tests, holding only the facts
    a test sets. A key it does not hold is absent, as for a record that does not exist."""

    def __init__(self) -> None:
        self.rows: dict[EvidenceKey, RecordFacts] = {}

    def facts(self, uow: object, keys: Sequence[EvidenceKey]) -> Mapping[EvidenceKey, RecordFacts]:
        return {k: self.rows[k] for k in keys if k in self.rows}


RECORD_FACTS = FakeRecordFacts()


class _Both:
    @staticmethod
    def register(registry: Any) -> None:
        _SENSORS.register(registry)
        rules_module.register(registry)
        registry.provide(RecordFactsPort, lambda _: RECORD_FACTS)


def edited_documents(
    monkeypatch: pytest.MonkeyPatch, edit: Callable[[dict[ConfigName, ConfigDocumentView]], None]
) -> None:
    """The station's first release with a test edit: the scratch copy of a document, never the shipped one (D15)."""
    original = base._documents

    def documents(db: Any) -> dict[ConfigName, ConfigDocumentView]:
        found: dict[ConfigName, ConfigDocumentView] = original(db)
        edit(found)
        return found

    monkeypatch.setattr(base, "_documents", documents)


def replaced(views: dict[ConfigName, ConfigDocumentView], name: ConfigName, value: object) -> None:
    raw = json.dumps(value, sort_keys=True).encode("utf-8")
    views[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    RECORD_FACTS.rows.clear()
    original = base.sensor_specs
    monkeypatch.setattr(base, "sensor_specs", lambda: (*original(), *rule_specs()))
    monkeypatch.setattr(base, "SENSOR_DEFAULTS", {**base.SENSOR_DEFAULTS, **RULE_DEFAULTS})
    monkeypatch.setattr(base, "sensors_module", _Both)
    yield


def wobble(sensor_type_id: str, interval_seconds: int, n: int) -> float:
    """A real sensor's reading moves. E23 cannot prove a constant value is not stuck (R-E23-2: a run longer than its
    bounded history is "unavailable" and never evaluated), so test feeds alternate by half the catalog's plausible
    rate over one interval: plausible, never stuck, and too small to move any limit decision."""
    catalog = json.loads((base.ROOT / "config" / "sensor-catalog.json").read_text(encoding="utf-8"))
    (kind,) = [t for t in catalog["sensor_types"] if t["id"] == sensor_type_id]
    return float(kind["plausibility"]["rate"]["max_per_second"]) * interval_seconds / 2 * (n % 2)


def build(tmp: Path, registry: dict[str, Any] | None = None, **kw: Any) -> base.Station:
    st = base.build_station(tmp, registry=registry, **kw)
    return st


def post(st: base.Station, rows: list[Any], key: str) -> Any:
    return st.client.post(
        f"/v1/adapters/{ADAPTER}/readings",
        json={"batch_id": "b_" + key, "readings": rows},
        headers=st.headers("adapter", key),
    )


def seconds(st: base.Station, n: int) -> Any:
    return st.clock.at - timedelta(seconds=n)


def restart(st: base.Station) -> None:
    """A new hub process over the same SQLite file: a fresh database object (its own bus, scheduler, guards and
    caches), a fresh event repository and release reader, and a new `create_app`. Nothing in memory survives except
    the injected test clock and settings, which a real restart reads again from the same sources."""
    from fastapi.testclient import TestClient

    from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
    from crew_health.events.adapters.repository import EventRepository, EventValidator
    from crew_health.kernel.testing import MemoryIds
    from crew_health.platform.testing.sqlite import database

    generation = len(list(st.tmp.glob("restart_*"))) + 1
    old = st.db
    old.close()
    db = database(old.path.parent, clock=old.clock, initialize=False)
    db.settings = old.settings
    db.ids = MemoryIds(identifiers=tuple(f"{generation}{n:05d}" for n in range(10000)))  # never reuses an id
    events = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=base.ROOT / "contracts/api/schemas"),
    )
    db.event_sink = events.append
    repository = ConfigRepository()
    db.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
    base._allow_sensor_inputs(db)
    build_dir = st.tmp / f"restart_{generation}"
    build_dir.mkdir()
    st.db = db
    st.app = base._app(db, st.security, base.config_artifacts(base._resources()), build_dir)
    st.client = TestClient(st.app, raise_server_exceptions=False)


def activate_release(
    st: base.Station,
    release_id: str,
    edit: Callable[[dict[ConfigName, ConfigDocumentView]], None] | None = None,
) -> None:
    """A second release proposed and activated through the real configuration service (E01), optionally with a
    scratch edit of the shipped documents, then its activation events delivered."""
    from crew_health.config.adapters.repository import ConfigRepository
    from crew_health.config.application.activation import ReleaseActivationService
    from crew_health.config.application.loader import ConfigLoader
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.config import CandidateRelease
    from crew_health.kernel.uow import Actor, SessionContext

    editor = SessionContext(
        "s_ed2", "editor", Actor("operator", "operator_sim"), None, frozenset({Role.EDITOR})
    )
    repository = ConfigRepository()
    service = ReleaseActivationService(
        repository,
        ConfigLoader(
            base._seed_checks(base.config_artifacts(base._resources())),
            max_document_bytes=st.db.settings.integer("commands.max_bytes"),
        ),
        st.db.ids,
        origin=lambda view: "SIM",
    )
    documents = base._documents(st.db)
    if edit is not None:
        edit(documents)
    with st.db.begin(actor=editor.actor, correlation_id="e24_" + release_id) as uow:
        service.propose(uow, editor, CandidateRelease(release_id, documents))
        etag = repository.revision(uow, release_id).etag  # the activation generation moves with every release
        service.activate(uow, editor, release_id=release_id, expected_etag=etag)
    st.dispatch()


def co2_act_hour(st: base.Station, key: str, *, channel: str = "ch_co2", value: float = 4.0) -> None:
    """An hour of CO2 readings above the one-hour act limit, posted live through the adapter and the real E23 gate:
    every minute the clock advances and that minute's twelve readings arrive, so the channel is never stale.
    CO2 is the only sensor type with sourced plausibility criteria (R-E23-2); every other type is "unavailable"
    and never evaluated, so a sensor act warning in a served test comes from CO2."""
    for minute in range(60):
        st.advance(seconds=60)
        rows = [
            base.reading(
                channel, minute * 12 + n + 1, value + wobble("co2", 5, minute * 12 + n), "mmHg",
                seconds(st, 60 - 5 * n),
            )
            for n in range(12)
        ]  # fmt: skip
        sent = post(st, rows, f"{key}_{minute:02d}")
        assert sent.status_code == 200, sent.text
