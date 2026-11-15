"""Real composition evidence. Provider prerequisites fail rather than inventing product coverage."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from crew_health.app.assembly import BuildCatalog, create_app
from crew_health.config.adapters.repository import PinnedRelease
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.health_schema import Health
from crew_health.kernel.testing import MemoryClock, MemoryIds
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings as storage_settings
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e18, pytest.mark.trace("INV-E18-10", "R22")]
ROOT = Path(__file__).resolve().parents[3]


def database(tmp_path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(
        tmp_path / "hub.sqlite",
        clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_1"),
        ids=MemoryIds(identifiers=tuple(str(index) for index in range(1000))),
        release=PinnedRelease("uninitialized", {}),
        settings=storage_settings(),
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=True,
    )


def test_unconfigured_real_hub_is_unavailable_and_refuses_evaluation_writes(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.core(ROOT / "hub/src/crew_health"),
            contract_path=ROOT / "contracts/api/openapi.yaml",
            schema_directory=ROOT / "contracts/api/schemas",
        )

        async def check() -> None:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                timeout = db.settings.integer("workers.hub.timeout_ms") / 1000
                response = await client.get("/v1/health", timeout=timeout)
                assert response.status_code == 200
                health = Health.model_validate_json(response.content)
                assert health.status == "degraded"
                assert health.assistant_state == "unavailable"
                response = await client.post("/v1/observations", content=b"{}", timeout=timeout)
                assert response.status_code == 401
                error = ErrorEnvelope.model_validate_json(response.content)
                assert error.code == "UNAUTHENTICATED" and error.current_state is None

        asyncio.run(check())
        assert (tmp_path / "hub.sqlite").is_file()
    finally:
        db.close()


@pytest.mark.parametrize(
    "acceptance,operation",
    [("V33", "getEvidencePassage"), ("V34", "startAssessment"), ("R46", "startEpisode")],
)
def test_required_product_operations_are_mounted(
    tmp_path: Path,
    acceptance: str,
    operation: str,
) -> None:
    db = database(tmp_path)
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.core(ROOT / "hub/src/crew_health"),
            contract_path=ROOT / "contracts/api/openapi.yaml",
            schema_directory=ROOT / "contracts/api/schemas",
        )
        schema = app.openapi()
        mounted = {
            row.get("operationId")
            for path in schema["paths"].values()
            for row in path.values()
            if isinstance(row, dict)
        }
        assert operation in mounted, (
            acceptance
            + " cannot run: real composition lacks "
            + operation
            + "; no substitute hub or authentication bypass is permitted"
        )
    finally:
        db.close()
