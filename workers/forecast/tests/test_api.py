import asyncio
from collections.abc import AsyncIterator
from threading import Event

import httpx
import pytest

from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.api.app import create_app
from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.service import ForecastRequest, ForecastService

from .conftest import FakeBackend, LocalClient


@pytest.mark.trace("E14-03", "V32", "V69")
def test_authenticated_roundtrip_and_only_two_routes(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    app = create_app(ForecastService(policy, backend, 1), WorkerSettings(), "secret")
    assert {route.path for route in app.routes} == {"/health", "/v1/forecast"}  # type: ignore[attr-defined]
    with LocalClient(app) as client:
        response = client.post(
            "/v1/forecast", json=forecast_request.model_dump(), headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "forecast"
        assert result["unit"] == "/min"
        assert result["steps"][0]["median"] == "14.25"
        assert result["context_days"] == 14
        assert result["interval_level"] == "0.8"
        assert client.get("/health").json()["state"] == "ready"
        for path in ("/docs", "/redoc", "/openapi.json", "/other"):
            assert client.get(path).status_code == 404


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize("token", [None, "secret"])
@pytest.mark.parametrize(
    "authorization", [None, "Bearer wrong", "Basic secret", "bearer secret", "Bearer secret extra"]
)
def test_unauthorized(
    policy: ForecastPolicy,
    backend: FakeBackend,
    forecast_request: ForecastRequest,
    token: str | None,
    authorization: str | None,
) -> None:
    with LocalClient(create_app(ForecastService(policy, backend, 1), WorkerSettings(), token)) as client:
        headers = {} if authorization is None else {"Authorization": authorization}
        assert (
            client.post("/v1/forecast", json=forecast_request.model_dump(), headers=headers).status_code
            == 401
        )
    assert backend.calls == 0


@pytest.mark.trace("E14-03", "V69")
def test_no_token_denies_even_matching_literal_none(policy: ForecastPolicy, backend: FakeBackend) -> None:
    with LocalClient(create_app(ForecastService(policy, backend, 1), WorkerSettings(), None)) as client:
        assert client.post("/v1/forecast", headers={"Authorization": "Bearer None"}).status_code == 401
        assert client.get("/health").json()["reason"] == "token_missing"


@pytest.mark.trace("E14-03", "V69")
def test_body_cap(policy: ForecastPolicy, backend: FakeBackend) -> None:
    with LocalClient(
        create_app(ForecastService(policy, backend, 1), WorkerSettings(max_body_bytes=1024), "secret")
    ) as client:
        assert (
            client.post(
                "/v1/forecast", content=b"x" * 1025, headers={"Authorization": "Bearer secret"}
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/v1/forecast", content=b"x" * 1024, headers={"Authorization": "Bearer secret"}
            ).status_code
            == 422
        )
        assert client.post("/v1/forecast", content=b"x" * 1025).status_code == 401
    assert backend.calls == 0


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize(
    "raw",
    [
        b"private secret invalid json",
        b'{"private":"very private"}',
        b"null",
        b'{"request_id":"bad id","unit":"/min","series":[]}',
        b'{"request_id":"id","unit":"/min","series":[{"mission_day":true,"value":"72"}]}',
        b'{"request_id":"id","unit":"/min","series":[{"mission_day":1,"value":72}]}',
        b'{"request_id":"id","unit":"/min","series":[{"mission_day":0,"value":"72"}]}',
        b'{"request_id":"id","unit":"/min","series":[],"extra":"private"}',
    ],
)
def test_malformed_request_never_echoes(policy: ForecastPolicy, backend: FakeBackend, raw: bytes) -> None:
    with LocalClient(create_app(ForecastService(policy, backend, 1), WorkerSettings(), "secret")) as client:
        response = client.post("/v1/forecast", content=raw, headers={"Authorization": "Bearer secret"})
        assert response.status_code == 422
        assert response.json() == {"code": "MALFORMED_REQUEST"}
    assert backend.calls == 0


@pytest.mark.trace("E14-03", "V32", "V69")
def test_truthful_health_without_patient_data(
    policy: ForecastPolicy, forecast_request: ForecastRequest
) -> None:
    service = ForecastService(policy, FakeBackend("model_hash_unpinned"), 1)
    with LocalClient(create_app(service, WorkerSettings(), "secret")) as client:
        health = client.get("/health").json()
        assert set(health) == {"state", "reason", "policy_version", "model"}
        assert health["reason"] == "model_hash_unpinned"
        assert health["state"] == "unavailable"
        response = client.post(
            "/v1/forecast", json=forecast_request.model_dump(), headers={"Authorization": "Bearer secret"}
        )
        assert response.json() == {
            "request_id": forecast_request.request_id,
            "status": "unavailable",
            "reason": "model_hash_unpinned",
        }


@pytest.mark.trace("E14-03", "V69")
def test_timeout_frees_the_inflight_slot_and_discards_the_late_result(
    policy: ForecastPolicy, backend: FakeBackend, forecast_request: ForecastRequest
) -> None:
    backend.release = Event()
    backend.block_calls = 1
    app = create_app(ForecastService(policy, backend, 1), WorkerSettings(timeout_ms=100), "secret")

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            try:
                response = await client.post(
                    "/v1/forecast",
                    json=forecast_request.model_dump(),
                    headers={"Authorization": "Bearer secret"},
                )
                assert backend.started.is_set()
                assert response.json() == {
                    "request_id": forecast_request.request_id,
                    "status": "unavailable",
                    "reason": "timeout",
                }
                next_request = forecast_request.model_copy(update={"request_id": "next-request"})
                again = await client.post(
                    "/v1/forecast",
                    json=next_request.model_dump(),
                    headers={"Authorization": "Bearer secret"},
                )
                assert again.json()["status"] == "forecast"
                assert again.json()["request_id"] == next_request.request_id
                assert backend.release is not None
                assert not backend.release.is_set()
                assert backend.threads[0].is_alive()
                assert backend.calls == 2
            finally:
                assert backend.release is not None
                backend.release.set()
                await asyncio.to_thread(backend.threads[0].join, 2)
            assert not backend.threads[0].is_alive()
            last_request = forecast_request.model_copy(update={"request_id": "after-late-result"})
            last = await client.post(
                "/v1/forecast",
                json=last_request.model_dump(),
                headers={"Authorization": "Bearer secret"},
            )
            assert last.json()["status"] == "forecast"
            assert last.json()["request_id"] == last_request.request_id
            assert response.json()["reason"] == "timeout"
            assert backend.calls == 3

    asyncio.run(exercise())


@pytest.mark.trace("E14-03", "V69")
def test_chunked_body_is_bounded_without_content_length(policy: ForecastPolicy, backend: FakeBackend) -> None:
    app = create_app(ForecastService(policy, backend, 1), WorkerSettings(max_body_bytes=1024), "secret")

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * 600
        yield b"x" * 600

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/forecast", content=chunks(), headers={"Authorization": "Bearer secret"}
            )
            assert response.status_code == 413

    asyncio.run(exercise())
