"""Recheck fault injection (AGENTS 4, review note): a process killed mid recheck leaves the accepted history intact
and the retry records the recheck once; clock jumps recompute due times from stored instants and never fill in a
missed recheck (E28 design failure table, clock row, T28-32)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName

from .plans_stack import NOW, PlansStack, obj, rows, stack

WATCH = "icl10_pulse_at_cameras"
INTERVAL = timedelta(minutes=15)
STACK_FILE = Path(__file__).with_name("plans_stack.py")

# The child builds the same stack, records the first check, then dies inside the second check's transaction:
# os._exit skips every handler and finalizer, as a power cut or a killed process would.
CHILD = """
import importlib.util, json, os, sys
from datetime import timedelta
from pathlib import Path
spec = importlib.util.spec_from_file_location("plans_stack_child", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["plans_stack_child"] = module
spec.loader.exec_module(module)
s = module.stack(Path(sys.argv[2]))
run = s.start()
first = s.act(run, "step_done", step_id=sys.argv[3], key="first")
print(json.dumps({"plan_run_id": first["plan_run_id"], "revision": first["revision"]}), flush=True)
s.clock.at = s.clock.at + timedelta(minutes=30)
def die(uow, event):
    os._exit(9)
s.db.event_sink = die
s.act(first, "step_done", step_id=sys.argv[3], key="second")
print("not killed", flush=True)
"""


def history(s: PlansStack, run_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in s.query(
            "SELECT occurrence, at FROM plans_run_recheck_entry WHERE plan_run_id=? AND step_id=? ORDER BY occurrence",
            (run_id, WATCH),
        )
    ]


def stamp(at: object) -> str:
    assert hasattr(at, "strftime")
    return str(at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


@pytest.mark.trace("T28-12", "E28-04", "R50")
def test_process_killed_mid_recheck_keeps_history_and_the_retry_records_once(tmp_path: Path) -> None:
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(STACK_FILE), str(tmp_path), WATCH],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert child.returncode == 9, child.stderr
    assert "not killed" not in child.stdout
    opened = obj(json.loads(child.stdout.splitlines()[0]))
    run_id = str(opened["plan_run_id"])

    s = stack(tmp_path)
    s.skip_ids(5000)  # The reopened test station must not reuse the dead process's deterministic ids.
    assert history(s, run_id) == [(1, stamp(NOW))]
    s.clock.at = NOW + timedelta(minutes=30)
    current = s.runs.get(s.session, run_id)
    assert current["revision"] == opened["revision"]
    retried = s.act(current, "step_done", step_id=WATCH, key="second")
    replayed = s.act(current, "step_done", step_id=WATCH, key="second")
    assert replayed["revision"] == retried["revision"]
    assert history(s, run_id) == [(1, stamp(NOW)), (2, stamp(NOW + timedelta(minutes=30)))]


def with_interval(s: PlansStack) -> None:
    """A configuration edit gives the watch step a relative recheck interval (no code change)."""
    plans = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    for plan in rows(plans["plans"]):
        for stage in rows(plan["stages"]):
            for step in rows(stage.get("steps", [])):
                if step["step_id"] == WATCH:
                    step["recheck"] = {
                        "cadence": None,
                        "n": None,
                        "interval_seconds": int(INTERVAL.total_seconds()),
                        "basis": "team default",
                        "to_be_set_by": None,
                    }
    s.replace_document(ConfigName.ONBOARD_PLANS, plans)


def recheck(wire: dict[str, object]) -> dict[str, object]:
    return next(r for r in rows(wire["rechecks"]) if r["step_id"] == WATCH)


def iso(at: object) -> str:
    assert hasattr(at, "isoformat")
    return str(at.isoformat()).replace("+00:00", "Z")


@pytest.mark.trace("T28-32", "T28-12")
def test_clock_jump_forward_recomputes_due_and_fills_nothing(tmp_path: Path) -> None:
    s = stack(tmp_path)
    with_interval(s)
    run = s.start()
    unknown = obj(obj(json.loads(s.docs[ConfigName.UI_COPY].raw))["designed_states"])["unknown"]
    assert (recheck(run)["due_at"], recheck(run)["due_text"]) == (None, unknown)
    first = s.act(run, "step_done", step_id=WATCH, key="first")
    assert recheck(first)["due_at"] == iso(NOW + INTERVAL)
    s.clock.at = NOW + timedelta(hours=3)  # Forward correction, past several intervals.
    later = s.runs.get(s.session, str(run["plan_run_id"]))
    assert recheck(later)["due_at"] == iso(NOW + INTERVAL)  # Stored instant, shown overdue, never refilled.
    assert history(s, str(run["plan_run_id"])) == [(1, stamp(NOW))]
    second = s.act(later, "step_done", step_id=WATCH, key="second")
    assert recheck(second)["due_at"] == iso(NOW + timedelta(hours=3) + INTERVAL)
    assert history(s, str(run["plan_run_id"])) == [(1, stamp(NOW)), (2, stamp(NOW + timedelta(hours=3)))]


@pytest.mark.trace("T28-32", "T28-12")
def test_clock_jump_backward_is_accepted_and_keeps_history_order(tmp_path: Path) -> None:
    s = stack(tmp_path)
    with_interval(s)
    run = s.start()
    first = s.act(run, "step_done", step_id=WATCH, key="first")
    s.clock.at = NOW - timedelta(hours=1)  # Backward correction.
    second = s.act(first, "step_done", step_id=WATCH, key="second")
    assert recheck(second)["due_at"] == iso(NOW - timedelta(hours=1) + INTERVAL)
    assert recheck(second)["done_at"] == iso(NOW - timedelta(hours=1))
    assert history(s, str(run["plan_run_id"])) == [(1, stamp(NOW)), (2, stamp(NOW - timedelta(hours=1)))]
