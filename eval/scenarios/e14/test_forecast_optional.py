"""E14-03: the real optional HTTP worker cannot change deterministic summaries.

Only the unbuilt E15 episode port uses EpisodeReader as a fake.
"""

import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from crew_health.trends.application.ports import BaselineSummary, ForecastTicket, Unavailable

from .support import (
    ROOT,
    ScenarioHub,
    history,
    stop_worker,
    worker_command,
    worker_environment,
)

pytestmark = pytest.mark.trace("E14-03", "R12", "V32", "V69", "V70")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def await_health(process: subprocess.Popen[bytes], base_url: str) -> None:
    for _ in range(100):
        assert process.poll() is None, "Forecast worker exited before becoming healthy"
        try:
            response = httpx.get(base_url + "/health", timeout=0.2)
        except httpx.TransportError:
            time.sleep(0.05)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.05)
    raise AssertionError("Forecast worker did not become healthy within the bounded startup wait")


def worker_settings(base_url: str, token: Path) -> dict[str, object]:
    return {
        "workers.forecast.enabled": True,
        "workers.forecast.base_url": base_url,
        "workers.forecast.token_path": str(token),
        "workers.forecast.timeout_ms": 1000,
    }


def test_real_worker_weights_absent_then_stopped_worker_retries(tmp_path: Path) -> None:
    token = tmp_path / "worker.token"
    token.write_text("e14-scenario-local-token", encoding="utf-8")
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = worker_environment() | {
        "CREW_HEALTH_FORECAST_HOST": "127.0.0.1",
        "CREW_HEALTH_FORECAST_PORT": str(port),
        "CREW_HEALTH_FORECAST_TOKEN_FILE": str(token),
    }
    with (tmp_path / "worker.log").open("wb") as log:
        process = subprocess.Popen(
            worker_command("crew_health_forecast"),
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            await_health(process, base_url)
            hub = ScenarioHub(tmp_path, overrides=worker_settings(base_url, token))
            history(hub)
            before = hub.read()
            assert isinstance(before.usual, BaselineSummary)
            first = hub.refresh()
            assert isinstance(first, ForecastTicket)
            assert first.generation == 1
            hub.finish()
            assert hub.latest() == Unavailable("forecast_worker", "weights_absent")
            assert hub.read() == before
            stop_worker(process)
            again = hub.refresh()
            assert isinstance(again, ForecastTicket)
            assert not again.reconnected
            assert again.generation == first.generation + 1
            assert again.task_id != first.task_id
            hub.finish()
            assert hub.latest() == Unavailable("forecast_worker", "worker_unreachable")
            assert hub.read() == before
        finally:
            stop_worker(process)


def test_unconfigured_worker_leaves_baseline_available(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path)
    history(hub)
    before = hub.read()
    assert isinstance(before.usual, BaselineSummary)
    assert before.usual.value.value == 30
    assert hub.refresh() == Unavailable("forecast_worker", "worker_not_configured")
    assert hub.latest() == Unavailable("trends", "no_forecast_requested")
    assert hub.read() == before
