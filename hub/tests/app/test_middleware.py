"""T-E01-13: operation security and bounded HTTP parsing without external services."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request

from crew_health.app.middleware import HubMiddleware, OperationCatalog
from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryIds
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.testing.sqlite import test_settings as station_settings

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-13", "D5", "D6")]


class TestClient:
    __test__ = False

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, **kwargs):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(run())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def catalog():
    return OperationCatalog.load(Path("contracts/api/openapi.yaml"))


def application(*, allow_mutations=True, allow_request=None):
    app = FastAPI()
    writes = []
    authentications = []
    session = SessionContext("session", "crew", Actor("person", "person"), "person", frozenset({Role.CREW}))

    def authenticate(request):
        authentications.append(request.url.path)
        if request.headers.get("Authorization") != "Bearer valid":
            raise ProblemError(ErrorCode.UNAUTHENTICATED, "private credential detail")
        return session

    def authorize(session, operation_id):
        if operation_id == "getConsentGrants":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "private grant detail")

    @app.get("/v1/health")
    def health(request: Request):
        return {"operation": request.state.operation_id, "session": request.state.session is None}

    @app.post("/v1/sessions", status_code=201)
    async def signin(request: Request):
        await request.body()
        return {"session": "issued"}

    @app.post("/v1/observations", status_code=201)
    async def observation(request: Request):
        writes.append(request.state.parsed_json)
        return {"accepted": True}

    @app.get("/v1/observations/{observation_id}")
    def get_observation(observation_id: str):
        return {"observation_id": observation_id}

    @app.post("/v1/kit-map/what-if")
    async def what_if(request: Request):
        return {"saved": False}

    @app.post("/v1/reports", status_code=201)
    async def upload(request: Request):
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
        return {"bytes": size}

    app.add_middleware(
        HubMiddleware,
        catalog=catalog(),
        settings=station_settings(),
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(100))),
        authenticate=authenticate,
        authorize=authorize,
        allow_mutations=lambda: allow_mutations,
        allow_request=allow_request,
    )
    return app, writes, authentications


def headers(**extra):
    return {
        "Authorization": "Bearer valid",
        "Idempotency-Key": "command",
        "Content-Type": "application/json",
        **extra,
    }


def test_public_health_and_signin_and_protected_auth_before_body():
    app, writes, auth = application()
    with TestClient(app) as client:
        assert client.get("/v1/health").json() == {"operation": "getHealth", "session": True}
        assert client.post("/v1/sessions", json={"credential": "local"}).status_code == 201
        denied = client.post("/v1/observations", content=b"not JSON")
        assert denied.status_code == 401
        assert denied.json()["message"] == "UNAUTHENTICATED"
        assert "private" not in denied.text and not writes
        assert auth == ["/v1/observations"]


@pytest.mark.parametrize(
    "raw", [b'{"value":1,"value":2}', b'{"value":NaN}', b'{"value":Infinity}', b'{"value":true,}', b"[]"]
)
def test_strict_json_rejects_ambiguous_nonfinite_or_wrong_root(raw):
    app, writes, _ = application()
    with TestClient(app) as client:
        result = client.post("/v1/observations", content=raw, headers=headers())
        assert result.status_code == 400
        assert result.json()["code"] == "MALFORMED_REQUEST"
        assert not writes


def test_configured_json_size_depth_and_expired_session():
    app, writes, _ = application()
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/observations", content=b'{"a":' + b"[" * 65 + b"0" + b"]" * 65 + b"}", headers=headers()
            ).status_code
            == 413
        )
        raw = b'"' + b"x" * station_settings().integer("commands.max_bytes") + b'"'
        assert client.post("/v1/observations", content=raw, headers=headers()).status_code == 413
        assert (
            client.post(
                "/v1/observations", json={}, headers=headers(Authorization="Bearer expired")
            ).status_code
            == 401
        )
        assert not writes


def test_mutation_exemptions_and_correlation_header():
    app, writes, _ = application()
    with TestClient(app) as client:
        assert (
            client.post("/v1/kit-map/what-if", json={}, headers={"Authorization": "Bearer valid"}).status_code
            == 200
        )
        assert (
            client.post("/v1/observations", json={}, headers={"Authorization": "Bearer valid"}).status_code
            == 400
        )
        result = client.post(
            "/v1/observations", json={}, headers=headers(**{"X-Correlation-Id": "correlation"})
        )
        assert result.status_code == 201 and result.headers["X-Correlation-Id"] == "correlation"
        assert writes == [{}]


def test_safe_mode_preserves_public_health_and_rejects_mutation():
    app, writes, _ = application(allow_mutations=False)
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.post("/v1/observations", json={}, headers=headers()).status_code == 500
        assert not writes


def test_catalog_requires_guarded_hash_and_complete_security(tmp_path):
    import hashlib

    import yaml

    original = catalog()
    assert not original.requires_ledger("createSession")
    assert not original.requires_ledger("kitMapWhatIf")
    assert original.requires_ledger("recordObservation")
    assert original.match("GET", "/v1/observations/obs_1").security == "sessionToken"
    assert original.match("POST", "/v1/adapters/adapter_1/readings").security == "adapterToken"
    raw = yaml.safe_load(Path("contracts/api/openapi.yaml").read_text())
    raw["paths"]["/v1/health"]["get"]["security"] = [{"unknown": []}]
    altered = yaml.safe_dump(raw).encode()
    path = tmp_path / "openapi.yaml"
    path.write_bytes(altered)
    with pytest.raises(ProblemError):
        OperationCatalog.load(path, expected_sha256=hashlib.sha256(altered).hexdigest())
    with pytest.raises(ProblemError):
        OperationCatalog.load(Path("contracts/api/openapi.yaml"), expected_sha256="0" * 64)


def test_multipart_stream_accepts_full_contract_file_and_rejects_excess_envelope():
    app, writes, _ = application()
    maximum = station_settings().integer("private.max_report_bytes")
    overhead = station_settings().integer("commands.multipart_overhead_bytes")
    prefix = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="report.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
    suffix = b"\r\n--boundary--\r\n"

    async def parts(count):
        yield prefix
        chunk = b"x" * 65536
        for _ in range(count // len(chunk)):
            yield chunk
        if count % len(chunk):
            yield chunk[: count % len(chunk)]
        yield suffix

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            multipart = headers(**{"Content-Type": "multipart/form-data; boundary=boundary"})
            success = await client.post("/v1/reports", content=parts(maximum), headers=multipart)
            assert success.status_code == 201
            assert success.json()["bytes"] == maximum + len(prefix) + len(suffix)
            denied = await client.post(
                "/v1/reports", content=parts(maximum + overhead + 1), headers=multipart
            )
            assert denied.status_code == 413
            assert denied.json()["code"] == "PAYLOAD_TOO_LARGE"
            malformed = await client.post(
                "/v1/reports", content=b"body", headers=headers(**{"Content-Type": "multipart/form-data"})
            )
            assert malformed.status_code == 400

    asyncio.run(run())
    assert not writes


def test_operation_aware_media_type_and_json_numbers():
    from decimal import Decimal

    app, writes, _ = application()
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/observations", content=b"hello", headers=headers(**{"Content-Type": "text/plain"})
            ).status_code
            == 415
        )
        result = client.post(
            "/v1/observations", content=b'{"value":0.1234567890123456789}', headers=headers()
        )
        assert result.status_code == 201
    assert writes == [{"value": Decimal("0.1234567890123456789")}]


def test_exact_decimal_canonical_identity_and_context_independence():
    from decimal import localcontext

    from crew_health.app.middleware import canonical_body, parse_json

    def body(raw):
        return canonical_body(parse_json(raw, max_bytes=1000, max_depth=10), max_bytes=1000, max_depth=10)

    assert body(b'{"a":1.0,"z":0.1}') == body(b'{"z":1e-1,"a":1}') == b'{"a":1,"z":0.1}'
    first = body(b'{"value":0.1234567890123456789}')
    second = body(b'{"value":0.1234567890123456788}')
    assert first != second
    assert first == body(b'{"value":1.234567890123456789e-1}')
    with localcontext() as context:
        context.prec = 2
        assert body(b'{"value":0.1234567890123456789}') == first
    assert body(b'{"value":-0}') == body(b'{"value":0.0}')


def test_startup_guard_allows_only_health_and_authenticates_protected_reads_first():
    serving = [False]
    app, writes, authentications = application(
        allow_request=lambda operation: serving[0] or operation.operation_id == "getHealth"
    )
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/observations/obs_1").status_code == 401
        assert (
            client.get("/v1/observations/obs_1", headers={"Authorization": "Bearer valid"}).status_code == 500
        )
        serving[0] = True
        assert (
            client.get("/v1/observations/obs_1", headers={"Authorization": "Bearer valid"}).status_code == 200
        )
    assert authentications == ["/v1/observations/obs_1"] * 3
    assert not writes
