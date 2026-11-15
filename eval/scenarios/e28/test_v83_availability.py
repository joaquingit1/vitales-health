"""V83 transactional HTTP acceptance in the E05 fallback with E32 SIM stock."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from crew_health.kernel.config import ConfigName
from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.application.ports import StockLevel
from stack import ROOT, Station, checked, load_json, obj, refused, run_step, steps, view_step

pytestmark = pytest.mark.trace("V83", "T28-02", "T28-03", "T28-04", "T28-07", "EI7")


def supply_candidate(station: Station, step_id: str) -> None:
    raw = station.raw(ConfigName.ONBOARD_PLANS)
    step = next(s for s in steps(raw) if s["step_id"] == step_id)
    obj(step["needs"])["supplies"] = [
        {"supply_id": "eye_rinse_water", "per_use": 1, "basis": "team default", "to_be_set_by": None}
    ]
    kit = station.raw(ConfigName.KIT_MAP)
    example = load_json(ROOT / "contracts/api/examples/kit-map.example.json")
    kit["supplies"] = example["supplies"]
    kit["stock"] = example["stock"]
    station.install("release_supply", {ConfigName.ONBOARD_PLANS: raw, ConfigName.KIT_MAP: kit})
    station.supplies.levels["eye_rinse_water"] = StockLevel(
        "eye_rinse_water", True, Quantity(Decimal(1), Unit("{rinse}")), Unit("{rinse}")
    )


@pytest.mark.parametrize("missing", ["instrument", "supply", "skill", "helper"])
def test_unavailable_step_refuses_before_every_effect(tmp_path: Path, missing: str) -> None:
    station = Station(tmp_path)
    target = "icl10_pulse_by_crewmate"
    if missing == "instrument":
        target = "icl10_pulse_at_cameras"
        station.equipment.removed.add("camera_station")
    elif missing == "supply":
        target = "icl10_note_episodes"
        supply_candidate(station, target)
        station.supplies.levels.clear()
    elif missing == "skill":
        station.crew.skills.clear()
    else:
        station.crew.absent.update(m.person_id for m in station.crew.members)
    run = station.start()
    response = station.client.get("/v1/conditions/ICL10/plan")
    assert response.status_code == 200, response.text
    step = view_step(response.json(), target)
    assert step["availability"] != "aboard" or step["who_can"] == []
    before = station.effects()
    refused(station.act(run, "step_done", step=target), 422, "STEP_NOT_AVAILABLE")
    assert station.effects() == before


def test_substitute_choice_is_open_and_eligible_completion_debits_once(tmp_path: Path) -> None:
    station = Station(tmp_path)
    supply_candidate(station, "icl10_pulse_at_oximeter")
    station.equipment.removed.add("camera_station")
    run = station.start()
    run = checked(station.act(run, "use_substitute", step="icl10_pulse_at_cameras"))
    assert run_step(run, "icl10_pulse_at_oximeter")["state"] == "open"
    assert station.query("plans_test_debit") == ()
    done = checked(station.act(run, "step_done", step="icl10_pulse_at_oximeter", key="substitute_done"))
    assert run_step(done, "icl10_pulse_at_oximeter")["state"] == "done"
    assert len(station.query("plans_test_debit")) == 1
    assert (
        checked(station.act(run, "step_done", step="icl10_pulse_at_oximeter", key="substitute_done")) == done
    )
    assert len(station.query("plans_test_debit")) == 1


def test_two_concurrent_completions_cannot_consume_the_last_unit_twice(tmp_path: Path) -> None:
    station = Station(tmp_path)
    supply_candidate(station, "icl10_note_episodes")
    raw = station.raw(ConfigName.ONBOARD_PLANS)
    targets = ("icl10_note_episodes", "icl10_avoid_triggers")
    principal = next(s for s in steps(raw) if s["step_id"] == targets[0])
    other = next(s for s in steps(raw) if s["step_id"] == targets[1])
    obj(other["needs"])["supplies"] = obj(principal["needs"])["supplies"]
    station.install("release_race", {ConfigName.ONBOARD_PLANS: raw})
    run = station.start("race_open")
    barrier = Barrier(2, timeout=10)
    before = station.effects()

    def complete(index: int) -> tuple[int, str]:
        barrier.wait()
        response = station.act(run, "step_done", step=targets[index], key="race_done_" + str(index))
        return response.status_code, response.text

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, range(2)))
    accepted = sum(status == 200 for status, _ in results)
    assert accepted == 1, results
    assert all(status in (200, 422, 500, 503) for status, _ in results), results
    assert len(station.query("plans_test_debit")) == 1
    after = station.effects()
    assert [len(after[i]) - len(before[i]) for i in (1, 2, 3, 4)] == [1, 1, 1, 1]
    final = checked(station.client.get("/v1/plan-runs/" + str(run["plan_run_id"])))
    assert final["revision"] == 2
    assert sorted(str(run_step(final, target)["state"]) for target in targets) == ["done", "open"]
