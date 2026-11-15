import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from crew_health.app.assembly import BuildCatalog, create_app
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-02", "T-E01-20", "R01")]
ROOT = Path(__file__).resolve().parents[3]


def test_missing_required_providers_keeps_health_and_denies_mutation(tmp_path: Path) -> None:
    db = database(tmp_path)
    app = create_app(
        database=db,
        build=BuildCatalog.core(ROOT / "hub/src/crew_health"),
        contract_path=ROOT / "contracts/api/openapi.yaml",
        schema_directory=ROOT / "contracts/api/schemas",
    )
    assert not app.state.runtime.serving

    async def run() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            health = await client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "degraded"
            assert health.json()["assistant_state"] == "unavailable"
            assert (await client.post("/v1/observations", content=b"{}")).json()["code"] == "UNAUTHENTICATED"

    asyncio.run(run())


def test_unknown_added_module_or_catalog_hash_fails_closed(tmp_path: Path) -> None:
    db = database(tmp_path)
    catalog = BuildCatalog.core(ROOT / "hub/src/crew_health")
    bad = BuildCatalog(
        catalog.root,
        {**catalog.inventory, "crew_health.not_installed.module": "0" * 64},
        catalog.registrations,
    )
    app = create_app(
        database=db,
        build=bad,
        contract_path=ROOT / "contracts/api/openapi.yaml",
        schema_directory=ROOT / "contracts/api/schemas",
    )
    assert not app.state.runtime.serving
    assert app.state.runtime.fault_code == "INTERNAL_ERROR"


def test_all_startup_phases_complete_for_explicit_sim_profile(tmp_path: Path) -> None:
    import runpy

    explicit_sim_station = runpy.run_path(str(ROOT / "eval/scenarios/E01/test_acceptance.py"))[
        "explicit_sim_station"
    ]

    from crew_health.app.registry import StartupPhase

    station = explicit_sim_station(tmp_path)
    station.build.verify(128)
    app = station.app()
    assert app.state.runtime.serving, app.state.runtime.fault_code
    assert app.state.runtime.completed_phases == list(StartupPhase)
    assert app.state.runtime.fault_code is None


def test_catalog_module_hash_mismatch_is_detected_before_startup(tmp_path: Path) -> None:
    import runpy

    static_core = runpy.run_path(str(ROOT / "eval/scenarios/E01/test_acceptance.py"))["static_core"]

    catalog = static_core(tmp_path)
    catalog.verify(128)
    module = catalog.root / "config/module.py"
    module.write_text(module.read_text() + "\n# Corrupt packaged module fixture\n")
    with pytest.raises(ValueError, match="installed modules differ"):
        catalog.verify(128)


def test_corrupt_or_missing_contract_retains_only_public_liveness(tmp_path: Path) -> None:
    db = database(tmp_path)
    app = create_app(
        database=db,
        build=BuildCatalog.core(ROOT / "hub/src/crew_health"),
        contract_path=tmp_path / "missing-openapi.yaml",
        schema_directory=ROOT / "contracts/api/schemas",
    )
    assert not app.state.runtime.serving

    async def run() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            response = await client.get("/v1/health")
            assert response.status_code == 200 and response.json()["status"] == "degraded"
            assert response.headers["X-Correlation-Id"]
            assert (await client.get("/v1/observations/obs_1")).status_code == 404

    asyncio.run(run())
