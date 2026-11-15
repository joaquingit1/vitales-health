"""Served demo hub: the plans ground_log line reads E29's link state through app/plans_seams.py, and step
step availability comes from guidance's facts bound in app/guidance_seams.py, not the unknown state."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from crew_health.app import cli, demo
from crew_health.app.plans_seams import PlansGroundLink, PlansInTime
from crew_health.plans.application.ground_log import GroundLinkReader, InTimeWords


def test_served_icl10_plan_ground_line_follows_the_ground_link(tmp_path: Path) -> None:
    directory = tmp_path / "demo"
    demo.reset(directory)
    state = json.loads((directory / demo.STATE).read_text(encoding="utf-8"))
    fixture = demo.Fixture.load(Path(state["root"]))
    seen: dict[str, object] = {}

    def server(app: FastAPI, host: str, port: int) -> None:
        resolver = app.state.runtime.resolver
        seen["reader"] = resolver.optional(GroundLinkReader)
        seen["in_time"] = resolver.optional(InTimeWords)
        client = TestClient(app)
        credential = json.loads((directory / "credentials.json").read_text(encoding="utf-8"))["mara"]
        signed = client.post(
            "/v1/sessions", json={"kind": "crew", "person_id": "mara", "credential": credential}
        )
        headers = {"authorization": "Bearer " + signed.json()["token"]}
        seen["link"] = client.get("/v1/ground-link", headers=headers).json()
        seen["plan"] = client.get("/v1/conditions/ICL10/plan", headers=headers)

    assert (
        cli.serve(
            database=directory / "hub.sqlite",
            settings=directory / "hub-settings.json",
            host="127.0.0.1",
            port=0,
            resources=demo.HubResources(directory / "resources"),
            origin="SIM",
            confirmation=cli.TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
            demo=fixture.presenter(),
            mission=fixture.mission(directory),
            server=server,
        )
        == 0
    )
    assert isinstance(seen["reader"], PlansGroundLink)
    assert isinstance(seen["in_time"], PlansInTime)
    link = seen["link"]
    assert isinstance(link, dict) and link["status"] == "up"
    response = seen["plan"]
    assert isinstance(response, Response)
    assert response.status_code == 200
    copy = json.loads((Path(state["root"]) / "config" / "ui-copy.json").read_text(encoding="utf-8"))
    stages = {stage["stage_id"]: stage for stage in response.json()["stages"]}
    ground = stages["ground_log"]
    assert ground["link_text"] == copy["plan_lines"]["ground_log_up"]
    assert ground["leaves_at"] is None
    unknown = copy["designed_states"]["unknown"]
    steps = [step for stage in stages.values() for step in stage.get("steps") or ()]
    by_id = {step["step_id"]: step for step in steps}
    assert steps and by_id["icl10_note_episodes"]["availability"] == "aboard"
    assert by_id["icl10_note_episodes"]["availability_text"] != unknown
