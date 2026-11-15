"""Demo act 2 (D03, V41 to V44, CONTRACTS 20 vector "Streak after submitting day 19") on the served hub.

The demo fixture is the package's own SIM crew and its examples: Mara checks days 1 to 18, submits day 19, and
her streak, longest run, total and the patches of 7 and 14 days come out as the examples state. The expected
values are read from `docs/product/api/examples/participation/`, taken from the contract, not the rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from e22_served import basis, bearer, run, sign_in, start
from httpx import AsyncClient

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "docs/product/api/examples/participation"
pytestmark = [pytest.mark.E22, pytest.mark.trace("D03", "V41", "V43", "V44", "R31", "R33")]


def example(name: str) -> dict[str, object]:
    value: dict[str, object] = json.loads((EXAMPLES / name).read_text("utf-8"))["value"]
    return value


def test_mara_streak_after_day_19_matches_the_contract_vector(tmp_path: Path) -> None:
    hub = start(tmp_path, day=2)
    for day in range(1, 19):
        assert hub.produce(basis("mara", day)).status == "queued"
    hub.set_day(19)
    # Submit day 19 as the producer would; a retry of the same submit changes nothing.
    first = hub.produce(basis("mara", 19))
    assert hub.produce(basis("mara", 19)).status == "duplicate"
    assert first.status == "queued"
    expected, patches = example("streak-after-submit.json"), example("patches.json")

    async def journey(client: AsyncClient) -> None:
        token = await sign_in(client, "mara")
        streak = (await client.get("/v1/me/streak", headers=bearer(token))).json()
        for field in (
            "patient_id",
            "mission_id",
            "origin_mode",
            "current_mission_day",
            "today",
            "current_streak_days",
            "longest_streak_days",
            "completed_days_total",
            "first_mission_day",
            "days",
        ):
            assert streak[field] == expected[field], field
        earned = (await client.get("/v1/me/patches", headers=bearer(await sign_in(client, "mara")))).json()
        wanted = patches["items"]
        assert isinstance(wanted, list)
        got = [(p["threshold"], p["patch_design_id"], p["reached_on_mission_day"]) for p in earned["items"]]
        assert got == [(w["threshold"], w["patch_design_id"], w["reached_on_mission_day"]) for w in wanted]
        # A missed day lowers only the current streak (V44): skip day 20, check day 21.
        hub.set_day(21)
        hub.produce(basis("mara", 21))
        after = (await client.get("/v1/me/streak", headers=bearer(await sign_in(client, "mara")))).json()
        assert (
            after["current_streak_days"],
            after["longest_streak_days"],
            after["completed_days_total"],
        ) == (1, 19, 20)
        assert [d["status"] for d in after["days"] if d["mission_day"] == 20] == ["missed"]

    run(hub.running, journey)
