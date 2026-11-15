"""A real E24 station for integration tests: the E23 station (real E01 mechanisms, mission and sensors over a
temporary on-disk SQLite) plus the real rules module. Only what E01 has no production provider for is a named fake
in e23_station (session source, unit registry, roster). Configuration is the shipped config/ documents."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sensors"))

import e23_station as base  # noqa: E402

from crew_health.contracts_gen.enums import ConfigName  # noqa: E402
from crew_health.kernel.config import ConfigDocumentView  # noqa: E402
from crew_health.rules import module as rules_module  # noqa: E402
from crew_health.rules.application.ports import RecordFactsPort  # noqa: E402
from crew_health.rules.domain.inputs import EvidenceKey, RecordFacts  # noqa: E402
from crew_health.rules.settings import DEFAULTS as RULE_DEFAULTS  # noqa: E402
from crew_health.rules.settings import specifications as rule_specs  # noqa: E402
from crew_health.sensors.application import evaluate as evaluate_module  # noqa: E402
from crew_health.sensors.domain.plausibility import PlausibilityResult  # noqa: E402

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
        found = original(db)
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
    # R-E23-2: E23 has no sourced plausibility criteria, so it evaluates nothing in any origin. These tests exercise
    # E24 on an evaluated channel, so the gate is answered "passed" here, in the test only, labeled as such.
    monkeypatch.setattr(
        evaluate_module,
        "assess",
        lambda kind, reading: PlausibilityResult("passed", "test_fixture_r_e23_2", None),
    )
    yield


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
