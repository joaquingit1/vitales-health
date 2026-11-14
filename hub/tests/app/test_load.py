"""T-E01-15: WAL liveness and honest refusal while the sole writer is occupied."""

from __future__ import annotations

import asyncio
import runpy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from crew_health.kernel.uow import Actor

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-15", "R25", "V35")]
ROOT = Path(__file__).resolve().parents[3]


def test_read_liveness_does_not_wait_for_occupied_writer(tmp_path: Path) -> None:
    fixture = runpy.run_path(str(ROOT / "eval/scenarios/E01/test_acceptance.py"))
    station = fixture["explicit_sim_station"](tmp_path)
    db, app = station.database, station.app()
    assert app.state.runtime.serving
    entered, release = threading.Event(), threading.Event()

    def occupy() -> None:
        with db.begin(actor=Actor("engine", "engine_load"), correlation_id="correlation_load"):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("bounded test writer not released")

    async def exercise() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await asyncio.wait_for(client.get("/v1/health"), timeout=2)
            assert response.status_code == 200
            assert not release.is_set()
            assert response.json()["assistant_state"] == "unavailable"

    with ThreadPoolExecutor(max_workers=1) as workers:
        worker = workers.submit(occupy)
        try:
            assert entered.wait(timeout=2)
            asyncio.run(exercise())
        finally:
            release.set()
        worker.result(timeout=2)
