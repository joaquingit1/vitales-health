"""Fail closed on the served hub: unknown time, missing origin and an evaluator fault never clear (AGENTS 2, D3, D13).

A real station raises the CO2 act warning from real readings. The evaluation inputs E23 delivers are captured at
E24's isolation boundary (`core.run_input`, the one entry every producer uses) and replayed with one property
broken, on the served hub's own storage and unit of work:

- unknown observation time or an untrusted clock, with every limit result saying the clear condition holds, for
  longer than the pinned dwell: no clear, the warning stays (3.6, D3);
- a missing origin: the engine cannot place the evidence, so the input becomes a durable pending evaluation, the
  warning stays, clears freeze and health reports EVALUATION_UNAVAILABLE (loud, D13);
- an evaluator fault: the same outcome (also `test_critical_paths.py::test_path6_...`, T-E24-072, 122).

A missing origin on the wire never reaches the engine at all: `on_sensor_evaluated` parses `OriginMode` before the
savepoint, so the producer's command fails as an input-validation error, which D13 says aborts the whole command
and is never swallowed.

T-E24-120, 072, 122, 123; INV-E24-07, INV-E24-16; D3, D13; H3, H9.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import patched  # noqa: F401
from test_station_co2 import station

from crew_health.kernel.scheduler import HealthState
from crew_health.rules.application import core
from crew_health.rules.domain.inputs import LimitResult, SensorEvaluatedInput

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-07", "INV-E24-16", "D3", "D13", "H3", "H9")]


def _co2(st: base.Station) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(
            "SELECT warning_id, revision, state FROM rules_warning WHERE definition_id = 'co2_above_limit'"
        ).fetchall()
    finally:
        connection.close()


def _pending(st: base.Station) -> int:
    connection = sqlite3.connect(st.db.path)
    try:
        return int(connection.execute("SELECT count(*) FROM rules_pending").fetchone()[0])
    finally:
        connection.close()


def _captured(st: base.Station, monkeypatch: pytest.MonkeyPatch) -> tuple[core.Runtime, SensorEvaluatedInput]:
    """The runtime and the last CO2 input E23 delivered while the station raised its warning."""
    seen: list[tuple[core.Runtime, SensorEvaluatedInput]] = []
    original = core.run_input

    def spy(rt: core.Runtime, uow: Any, inp: Any) -> None:
        if isinstance(inp, SensorEvaluatedInput):
            seen.append((rt, inp))
        original(rt, uow, inp)

    monkeypatch.setattr(core, "run_input", spy)
    from test_station_co2 import feed

    st.advance(seconds=60)
    feed(st, 4.0, 721)
    monkeypatch.setattr(core, "run_input", original)
    assert seen
    return seen[-1]


def _holding(inp: SensorEvaluatedInput) -> tuple[LimitResult, ...]:
    return tuple(replace(r, crossed=False, clear_holds=True) for r in inp.results)


@pytest.mark.trace("T-E24-120")
@pytest.mark.parametrize(
    "broken", ["control", "unknown_time", "future_time", "clock_uncertain", "clock_unknown"]
)
def test_unknown_or_untrusted_time_never_clears_on_the_served_hub(
    patched: None,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
) -> None:
    st = station(tmp_path)
    rt, inp = _captured(st, monkeypatch)
    before = _co2(st)
    for n in range(30):  # 30 minutes of "clear holds", three times the pinned 600 s dwell
        st.advance(seconds=60)
        observed = inp.observed
        normalized = replace(observed.normalized, original_at=st.clock.at, corrected_at=st.clock.at)
        if broken in ("unknown_time", "future_time"):
            normalized = replace(normalized, state="unknown" if broken == "unknown_time" else "future")
        step = replace(
            inp, observed=replace(observed, normalized=normalized), results=_holding(inp),
            reading_id=f"rdg_broken_{n}", receive_reference=None,
            clock_state={"clock_uncertain": "uncertain", "clock_unknown": "unknown"}.get(broken, "trusted"),
        )  # fmt: skip
        with st.begin() as uow:
            core.run_input(rt, uow, step)
    if broken == "control":  # the same evidence with a known time and a trusted clock does clear: not vacuous
        assert _co2(st)[0][2] == "cleared"
    else:
        assert _co2(st) == before and before[0][2] == "raised"


@pytest.mark.trace("T-E24-072", "T-E24-122")
def test_a_missing_origin_or_a_fault_becomes_pending_degraded_and_never_clears(
    patched: None,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    st = station(tmp_path)
    rt, inp = _captured(st, monkeypatch)
    before = _co2(st)
    assert _pending(st) == 0
    st.advance(seconds=60)
    orphan = replace(inp, origin_mode=None, results=_holding(inp), reading_id="rdg_no_origin")  # type: ignore[arg-type]
    with st.begin() as uow:
        core.run_input(rt, uow, orphan)  # converted at the isolation boundary, never propagated
    assert _co2(st) == before
    assert _pending(st) >= 1
    health = {c.component: c.code for c in st.resolver().require(HealthState).snapshot().components}
    assert health.get("rules") == "EVALUATION_UNAVAILABLE"
    for n in range(15):  # fresh, trusted, holding evidence while the pending row exists: clears stay frozen
        st.advance(seconds=60)
        good = replace(
            inp, results=_holding(inp), reading_id=f"rdg_good_{n}",
            observed=replace(inp.observed, normalized=replace(
                inp.observed.normalized, original_at=st.clock.at, corrected_at=st.clock.at)),
        )  # fmt: skip
        with st.begin() as uow:
            core.run_input(rt, uow, good)
    assert _co2(st) == before  # nothing cleared while an input of the channel is pending
    assert timedelta(0) < st.clock.at - base.START
