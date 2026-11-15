"""HTTP helpers shared by the E02 station tests. A uniquely named module: `test_http` also exists in other test
directories, and the whole suite resolves test modules by bare name."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from e02_station import START, Running, credential, release_id
from httpx import ASGITransport, AsyncClient, Response

from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.session_schema import Session

type Journey = Callable[[AsyncClient], Awaitable[None]]


def run(running: Running, journey: Journey) -> None:
    async def main() -> None:
        async with AsyncClient(transport=ASGITransport(app=running.app), base_url="http://station") as client:
            await journey(client)

    asyncio.run(main())


async def sign_in(
    client: AsyncClient, person: str | None, *, kind: str = "crew", secret: str | None = None
) -> str:
    body: dict[str, object] = {"kind": kind, "credential": secret or credential(person or "stn_galley")}
    if person is not None:
        body["person_id"] = person
    response = await client.post("/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    token = Session.model_validate(response.json()).token
    assert token is not None
    return token


def bearer(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, **headers}


def problem(response: Response, status: int, code: str) -> ErrorEnvelope:
    assert response.status_code == status, response.text
    error = ErrorEnvelope.model_validate(response.json())
    assert error.code == code
    return error


def observation(patient: str, value: int = 50) -> dict[str, object]:
    return {
        "patient_id": patient,
        "code": "indicator",
        "value": value,
        "unit": "1",
        "observed_at": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin_mode": "SIM",
        "acquisition_method": "self_report",
    }


async def record(client: AsyncClient, token: str, patient: str, key: str) -> Response:
    headers = bearer(token, **{"Idempotency-Key": key, "Config-Release": release_id()})
    return await client.post("/v1/observations", headers=headers, json=observation(patient))
