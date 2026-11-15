"""DEMO-PLAN shot C on the served demo hub (production composition, real HTTP routes, E17 runtime port, E12 gateway,
E05 request): "My heart has been racing since this morning".

The model is E17's own pinned stub responder behind the same ModelServices port the live llama.cpp runtime uses
(test like you fly: everything from the inference port inward is the production code). The live-model run of the
same flow is `test_live_model_intake` below, marked slow and opt in.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_health.app import cli, demo
from crew_health.app.station import HubResources
from crew_health.model.application.ports import ModelInstallation, StubInstallation

ROOT = Path(__file__).resolve().parents[3]
RELEASE = "mdl_shot_c_stub"
MESSAGE = "My heart has been racing since this morning"


class _Tokens:
    """E17 TokenCounter double for the stub profile: one token per UTF-8 byte (never fewer than the tokenizer)."""

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


def _manifest() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "hub/tests"))
    from model.conftest import manifest

    return cast(dict[str, Any], manifest.__wrapped__())  # type: ignore[attr-defined]


def _install(directory: Path, script: Mapping[str, object]) -> tuple[ModelInstallation, StubInstallation]:
    root, data = directory / "model", directory / "model-data"
    shutil.copytree(ROOT / "model", root, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    raw = json.dumps(script, sort_keys=True).encode("utf-8")
    document = _manifest()
    document.update(release_id=RELEASE, stub_script_sha256=hashlib.sha256(raw).hexdigest())
    (root / "releases").mkdir(exist_ok=True)
    (root / "releases" / f"{RELEASE}.json").write_text(json.dumps(document), encoding="utf-8")
    (data / "models").mkdir(parents=True)
    (data / "models/active.json").write_text(json.dumps({"release_id": RELEASE}), encoding="utf-8")
    installation = ModelInstallation(str(root), str(data), "demo", "/nonexistent/llama-server", 18099)
    return installation, StubInstallation(raw, "shot_c", 0, _Tokens())


def _script(steps: list[dict[str, object]]) -> dict[str, object]:
    return {
        "scenario": "shot_c",
        "entries": [
            {"conversation_ordinal": 0, "phase": "intake", "step_index": index, **step}
            for index, step in enumerate(steps)
        ],
    }


KIT_CHECK = {
    "final": None,
    "tool_calls": [{"id": "m1", "name": "get_capabilities", "arguments": {"codes": ["pulse", "ecg_rhythm"]}}],
}


def _propose(reason: str) -> dict[str, object]:
    arguments = {
        "capability_code": "pulse",
        "reason": reason,
        "decision_affected": "Whether your heart is beating fast right now",
        "body_region": "heart",
    }
    return {
        "final": None,
        "tool_calls": [{"id": "m2", "name": "propose_measurement", "arguments": arguments}],
    }


def _serve(
    directory: Path,
    run: Callable[[TestClient], None],
    model: tuple[ModelInstallation, StubInstallation] | None,
) -> None:
    state = json.loads((directory / demo.STATE).read_text(encoding="utf-8"))
    fixture = demo.Fixture.load(Path(state["root"]))

    def server(app: FastAPI, host: str, port: int) -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            run(client)

    code = cli.serve(
        database=directory / "hub.sqlite",
        settings=directory / "hub-settings.json",
        host="127.0.0.1",
        port=0,
        resources=HubResources(directory / "resources"),
        origin="SIM",
        confirmation=cli.TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
        server=server,
        demo=fixture.presenter(),
        mission=fixture.mission(directory),
        model=None if model is None else model[0],
        stub=None if model is None else model[1],
    )
    assert code == 0


def _sign_in(client: TestClient, directory: Path, kind: str, person: str) -> dict[str, str]:
    credential = json.loads((directory / demo.CREDENTIALS).read_text(encoding="utf-8"))[person]
    body = {"kind": kind, "credential": credential, **({"person_id": person} if kind == "crew" else {})}
    signed = client.post("/v1/sessions", json=body)
    assert signed.status_code in (200, 201), signed.text
    return {"Authorization": "Bearer " + signed.json()["token"]}


def _key(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": secrets.token_hex(16)}


def _runtime(client: TestClient, directory: Path, action: str, wait_for: str) -> None:
    fixture = demo.Fixture.load(
        Path(json.loads((directory / demo.STATE).read_text(encoding="utf-8"))["root"])
    )
    operator = _sign_in(client, directory, "operator", fixture.operator_id)
    answer = client.post("/v1/assistant/runtime", json={"action": action}, headers=_key(operator))
    assert answer.status_code == 200, answer.text
    for _ in range(600):
        if client.get("/v1/assistant/status", headers=operator).json()["state"] == wait_for:
            return
        time.sleep(0.05)
    raise AssertionError("assistant never reached " + wait_for)


def seed_episode(app: Any, person: str, concern: str) -> str:
    """E04's startEpisode route is not built on main, so the fixture opens Mara's concern through E15's own
    EpisodeKernel.create, as the person, exactly as E04 would (fixture data, SIM origin)."""
    from datetime import UTC, datetime

    from crew_health.episodes.application.dto import NewEpisode, PersonRef
    from crew_health.episodes.application.ports import EpisodeKernel
    from crew_health.episodes.domain.onset import Onset
    from crew_health.identity.application.service import IdentityService
    from crew_health.kernel.uow import Actor, UnitOfWorkFactory

    resolver = app.state.runtime.resolver
    identity = resolver.require(IdentityService)
    with identity.deps.snapshots.snapshot() as view:
        mission, origin = identity.partition(view)
    with resolver.require(UnitOfWorkFactory).begin(
        actor=Actor("person", person), correlation_id="corr_seed"
    ) as uow:
        ref = resolver.require(EpisodeKernel).create(
            uow,
            NewEpisode(
                patient_id=person,
                mission_id=mission,
                origin_mode=origin,
                concern_text=concern,
                onset=Onset(None, "unknown"),
                created_by=PersonRef(person, person),
                questionnaire_version="0" * 64,
                red_flag_ids=(),
                config_release_id=uow.release.release_id,
                received_at=datetime.now(UTC),
            ),
        )
    return str(ref.episode_id)


def _episode_conversation(client: TestClient, mara: dict[str, str]) -> str:
    episode_id = seed_episode(client.app, "mara", MESSAGE)
    opened = client.post(
        "/v1/assistant/conversations",
        json={"context": {"kind": "episode", "ref_id": episode_id}},
        headers=_key(mara),
    )
    assert opened.status_code == 201, opened.text
    return str(opened.json()["conversation_id"])


def _turn(client: TestClient, mara: dict[str, str], conversation: str, seconds: float) -> dict[str, Any]:
    sent = client.post(
        f"/v1/assistant/conversations/{conversation}/messages", json={"text": MESSAGE}, headers=_key(mara)
    )
    assert sent.status_code == 202, sent.text
    assert sent.json()["role"] == "person" and sent.json()["text"] == MESSAGE
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = client.get(f"/v1/assistant/conversations/{conversation}", headers=mara).json()
        if body["active_turn_id"] is None and len(body["messages"]) >= 2:
            return cast(dict[str, Any], body["messages"][-1])
        time.sleep(0.05)
    raise AssertionError("turn did not finish")


def _turn_record(directory: Path, turn_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import sqlite3

    with sqlite3.connect(directory / "hub.sqlite") as db:
        calls = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM assistant_tool_call WHERE turn_id=? ORDER BY rowid", (turn_id,)
            )
        ]
        effects = [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM assistant_applied_effect WHERE turn_id=?", (turn_id,))
        ]
    return calls, effects


@pytest.mark.trace("D05", "D06", "D30", "R17", "I4", "I5")
def test_shot_c_kit_check_reply_and_a_proposed_pulse_request_the_crew_confirms(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    demo.reset(directory)
    reason = "Your pulse I can measure. Your rhythm I can't: that needs an ECG."
    model = _install(tmp_path, _script([KIT_CHECK, _propose(reason)]))

    def run(client: TestClient) -> None:
        _runtime(client, directory, "start", "running")
        mara = _sign_in(client, directory, "crew", "mara")
        reply = _turn(client, mara, _episode_conversation(client, mara), 30)
        assert reply["status"] == "complete", reply
        assert reply["role"] == "assistant" and reply["text"] is None
        action = reply["action"]
        assert action["action"] == "request_measurement" and action["capability_code"] == "pulse"
        assert action["reason"] == reason
        assert reply["limitations"] == ["Heart rhythm (ECG): This kit can't: needs an ECG"]
        calls, effects = _turn_record(directory, reply["turn_id"])
        assert [(c["name"], c["status"]) for c in calls] == [
            ("get_capabilities", "ok"),
            ("propose_measurement", "ok"),
        ]
        kit = {item["code"]: item["status"] for item in calls[0]["data"]["items"]}
        assert kit == {"ecg_rhythm": "unavailable", "pulse": "available"}
        [request_id] = effects[0]["request_ids"]
        # The assistant only proposed it: E05 holds it as proposed until the crew member acts on it.
        request = client.get(f"/v1/measurement-requests/{request_id}", headers=mara)
        assert request.status_code == 200, request.text
        assert request.json()["state"] == "proposed"
        accepted = client.post(
            f"/v1/measurement-requests/{request_id}/actions",
            json={"action": "accept"},
            headers={**_key(mara), "If-Match": request.headers["ETag"]},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] != "proposed"

    _serve(directory, run, model)


@pytest.mark.trace("I5", "R17")
def test_a_value_no_tool_returned_is_refused_and_nothing_is_requested(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    demo.reset(directory)
    model = _install(tmp_path, _script([KIT_CHECK, _propose("Your pulse is 142 per minute, a fast rhythm.")]))

    def run(client: TestClient) -> None:
        _runtime(client, directory, "start", "running")
        mara = _sign_in(client, directory, "crew", "mara")
        reply = _turn(client, mara, _episode_conversation(client, mara), 30)
        assert (reply["role"], reply["status"], reply["error_code"]) == (
            "notice",
            "rejected",
            "MODEL_OUTPUT_REJECTED",
        )
        assert reply["action"] is None
        _, effects = _turn_record(directory, reply["turn_id"])
        assert effects == []

    _serve(directory, run, model)


@pytest.mark.trace("I4")
def test_a_tool_outside_the_phase_ends_the_turn_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    demo.reset(directory)
    lower = {
        "final": None,
        "tool_calls": [{"id": "m1", "name": "propose_review", "arguments": {"note": "lower it"}}],
    }
    model = _install(tmp_path, _script([lower]))

    def run(client: TestClient) -> None:
        _runtime(client, directory, "start", "running")
        mara = _sign_in(client, directory, "crew", "mara")
        reply = _turn(client, mara, _episode_conversation(client, mara), 30)
        assert (reply["role"], reply["status"], reply["error_code"]) == (
            "notice",
            "rejected",
            "MODEL_OUTPUT_REJECTED",
        )

    _serve(directory, run, model)


@pytest.mark.trace("I3", "D25")
def test_with_the_model_stopped_the_send_is_refused_honestly_and_help_still_opens(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    demo.reset(directory)
    model = _install(tmp_path, _script([KIT_CHECK]))

    def run(client: TestClient) -> None:
        _runtime(client, directory, "start", "running")
        _runtime(client, directory, "stop", "stopped")
        mara = _sign_in(client, directory, "crew", "mara")
        conversation = _episode_conversation(client, mara)
        sent = client.post(
            f"/v1/assistant/conversations/{conversation}/messages", json={"text": MESSAGE}, headers=_key(mara)
        )
        assert sent.status_code == 503, sent.text
        assert sent.json()["code"] == "MODEL_UNAVAILABLE"
        body = client.get(f"/v1/assistant/conversations/{conversation}", headers=mara).json()
        assert body["messages"] == [] and body["active_turn_id"] is None
        helped = client.post(
            "/v1/help-sessions",
            json={"subject": {"kind": "self"}, "entry": {"kind": "button"}},
            headers=_key(mara),
        )
        assert helped.status_code == 201, helped.text

    _serve(directory, run, model)


@pytest.mark.slow
@pytest.mark.trace("D05", "D06")
def test_live_model_intake(tmp_path: Path) -> None:
    """The same flow against the pinned HuatuoGPT-3-9B Q4 weights through llama.cpp. Opt in with
    CREW_HEALTH_LIVE_MODEL=<directory holding model/ and data/ staged by an eval release>. The outcome is whatever
    the real model produced, stored honestly: a verified request, or a rejected or timed out notice."""
    staged = os.environ.get("CREW_HEALTH_LIVE_MODEL")
    if not staged:
        pytest.skip("live model not staged (CREW_HEALTH_LIVE_MODEL)")
    directory = tmp_path / "hub"
    demo.reset(directory)
    base = Path(staged)
    installation = ModelInstallation(
        str(base / "model"), str(base / "data"), "eval", "/opt/homebrew/bin/llama-server", 18097
    )

    def run(client: TestClient) -> None:
        _runtime(client, directory, "start", "running")
        mara = _sign_in(client, directory, "crew", "mara")
        reply = _turn(client, mara, _episode_conversation(client, mara), 240)
        assert reply["status"] in {"complete", "rejected", "timed_out", "failed"}
        if reply["status"] == "complete":
            assert reply["action"]["action"] in {"request_measurement", "stop_incomplete"}
        else:
            assert reply["role"] == "notice" and reply["error_code"] is not None
        _runtime(client, directory, "stop", "stopped")

    state = json.loads((directory / demo.STATE).read_text(encoding="utf-8"))
    fixture = demo.Fixture.load(Path(state["root"]))

    def server(app: FastAPI, host: str, port: int) -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            run(client)

    assert (
        cli.serve(
            database=directory / "hub.sqlite",
            settings=directory / "hub-settings.json",
            host="127.0.0.1",
            port=0,
            resources=HubResources(directory / "resources"),
            origin="SIM",
            confirmation=cli.TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
            server=server,
            demo=fixture.presenter(),
            mission=fixture.mission(directory),
            model=installation,
        )
        == 0
    )
