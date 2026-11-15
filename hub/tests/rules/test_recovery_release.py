"""A pending crossed sensor input recovered after a new release activated still raises (D13, E24 5.5).

D13: recovery re-evaluates pending inputs under their original observation time and release. A real station feeds a
CO2 hour above the limit while the evaluator is broken, so the crossing is captured as a pending row. A second
release then activates through the real configuration service before the fault is fixed. Recovery must raise the
act warning pinned to the release the input was evaluated under, never refuse it as stale.

T-E24-122, T-E24-072; D13; INV-E24-16.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from crew_health.rules.domain import engine

from . import rules_station as base
from .e24_station import activate_release
from .test_station_co2 import feed, station

pytestmark = [pytest.mark.E24, pytest.mark.trace("D13", "INV-E24-16")]


def _rows(st: base.Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.mark.trace("T-E24-122", "T-E24-072")
def test_a_pending_crossing_recovered_after_an_activation_raises_under_its_original_release(
    patched: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    st = station(tmp_path, send=False)
    original = engine.evaluate
    with monkeypatch.context() as broken:
        broken.setattr(engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        feed(st, 4.0, 1)
        assert _rows(st, "SELECT count(*) FROM rules_warning") == [(0,)]
        assert _rows(st, "SELECT count(*) FROM rules_pending")[0][0] >= 1
        activate_release(st, "release_two")
    assert engine.evaluate is original
    assert _rows(st, "SELECT release_id FROM config_activation WHERE singleton = 1") == [("release_two",)]
    for _ in range(80):  # one recovery row per unit of work, past each back-off of the failed attempts
        st.advance(seconds=300)
        st.dispatch()
        if not _rows(st, "SELECT count(*) FROM rules_pending WHERE state != 'stuck'")[0][0]:
            break
    assert _rows(st, "SELECT state, last_code FROM rules_pending") == []
    raised = _rows(
        st,
        "SELECT level, state, config_release_id FROM rules_warning WHERE definition_id = 'co2_above_limit'",
    )
    assert raised == [("act", "raised", "release_e23")]
