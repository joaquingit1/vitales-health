"""Real composition evidence for E18.

The hub refuses unconfigured writes. The product suites run only when the composed hub mounts every
operation E18's driver calls; otherwise they are `not_run` with the missing operations named, never a
substitute hub or an authentication bypass.
"""

import asyncio
import sys
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

pytestmark = [pytest.mark.trace("INV-E18-10", "R22")]
ROOT = Path(__file__).resolve().parents[3]
# E18 is its own uv project; its readiness rule is standard library only, so the hub environment can load it.
if str(ROOT / "eval/bench/src") not in sys.path:
    sys.path.insert(0, str(ROOT / "eval/bench/src"))
from crew_health_bench.domain.readiness import DRIVER_OPERATIONS, product_suite_readiness  # noqa: E402


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
                assert error.code == "UNAUTHENTICATED"
                assert error.current_state is None

        asyncio.run(check())
        assert (tmp_path / "hub.sqlite").is_file()
    finally:
        db.close()


@pytest.mark.trace("E18-03", "V33", "V34", "R46", "INV-E18-05")
def test_product_suites_follow_the_operations_the_real_composition_mounts(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.core(ROOT / "hub/src/crew_health"),
            contract_path=ROOT / "contracts/api/openapi.yaml",
            schema_directory=ROOT / "contracts/api/schemas",
        )
        mounted = {
            str(row.get("operationId"))
            for path in app.openapi()["paths"].values()
            for row in path.values()
            if isinstance(row, dict) and row.get("operationId")
        }
    finally:
        db.close()
    contract = (ROOT / "contracts/api/openapi.yaml").read_text(encoding="utf-8")
    for operation in DRIVER_OPERATIONS:
        assert "operationId: " + operation in contract, operation + " is not in the API contract"
    readiness = product_suite_readiness(mounted, harness_available=True)
    expected_missing = tuple(name for name in DRIVER_OPERATIONS if name not in mounted)
    assert readiness.missing_operations == expected_missing
    assert readiness.ready == (not expected_missing)
    if expected_missing:
        assert readiness.reason == "provider_missing"
