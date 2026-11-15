import asyncio
from collections.abc import Sequence
from decimal import Decimal
from threading import Event, Lock, Thread, current_thread

import httpx
import pytest
from fastapi import FastAPI

from crew_health_forecast.adapters.settings import default_policy_path
from crew_health_forecast.application.policy import ForecastPolicy, load_policy
from crew_health_forecast.application.ports import BackendStatus
from crew_health_forecast.application.service import ForecastRequest, SeriesPoint


class FakeBackend:
    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.fail = False
        self.invalid = False
        self.started = Event()
        self.release: Event | None = None
        self.block_calls: int | None = None
        self.threads: list[Thread] = []
        self._lock = Lock()
        self.context: tuple[Decimal, ...] = ()
        self.calls = 0

    def status(self) -> BackendStatus:
        return BackendStatus(self.reason is None, self.reason, "test/model", "test-revision", "a" * 64)

    def predict(
        self, context: Sequence[Decimal], horizon: int, low: Decimal, high: Decimal
    ) -> list[tuple[Decimal, Decimal, Decimal]]:
        with self._lock:
            self.calls += 1
            call = self.calls
            self.threads.append(current_thread())
            self.context = tuple(context)
        self.started.set()
        blocked = self.block_calls is None or call <= self.block_calls
        if blocked and self.release is not None and not self.release.wait(timeout=5):
            raise RuntimeError("test_backend_blocked")
        if self.fail:
            raise RuntimeError("private input must not escape")
        value = context[-1]
        if self.invalid:
            return [(value + 1, value, value + 2)] * horizon
        return [(value - 1, value, value + 1)] * horizon


@pytest.fixture
def policy() -> ForecastPolicy:
    return load_policy(default_policy_path())


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def forecast_request(policy: ForecastPolicy) -> ForecastRequest:
    return ForecastRequest(
        request_id="test:forecast_request-1",
        policy_version=policy.version,
        unit="/min",
        series=[
            SeriesPoint(mission_day=day, value=f"{day}.25") for day in range(1, policy.min_context_days + 1)
        ],
    )


class LocalClient:
    """Synchronous test convenience over the supported asynchronous ASGI transport."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def __enter__(self) -> LocalClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        return self._request("GET", url)

    def post(
        self,
        url: str,
        *,
        json: object = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("POST", url, json=json, content=content, headers=headers)

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async def invoke() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, url, json=json, content=content, headers=headers)

        return asyncio.run(invoke())
