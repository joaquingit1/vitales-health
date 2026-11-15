# mypy: disable-error-code="type-abstract"
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.runtime_probe import RuntimeIdentity
from crew_health.model.application.ports import (
    Deadline,
    InferencePort,
    ModelInstallation,
    ModelLaunchSpecPort,
    ModelReleaseReader,
    ModelRuntimePort,
    PromptPort,
    StepOk,
    StepRequest,
    StubInstallation,
)
from crew_health.model.module import register
from crew_health.model.settings import specifications

from .test_integration import live


@pytest.mark.trace("R22", "T-E17-080")
def test_real_registry_resolves_every_canonical_model_port_without_model_installed() -> None:
    registry = DeclarationRegistry(max_ports=32, max_hooks=128)
    register(registry)
    for port in (ModelReleaseReader, PromptPort, InferencePort, ModelRuntimePort, ModelLaunchSpecPort):
        assert port in registry.providers
    resolver = registry.resolve()
    assert resolver.require(ModelReleaseReader).active() is None
    assert resolver.require(ModelRuntimePort).probe(timeout_s=1).detail_code == "model_unavailable"


@pytest.mark.trace("R22", "T-E17-002")
def test_installed_release_reader_is_wired_through_real_provider_class(store: ManifestStore) -> None:
    values = {
        "model.max_bytes": 1048576,
        "model.max_chunks": 256,
        "model.max_tools": 8,
        "model.connect_timeout_ms": 1000,
        "model.cancel_poll_ms": 10,
        "model.memory_budget_bytes": 7500000000,
        "model.cpu_budget": 6,
        "model.nice": 10,
        "model.stop_grace_ms": 1000,
    }
    registry = DeclarationRegistry(max_ports=32, max_hooks=128)
    settings = MemorySettings(specifications(), values)
    registry.provide(HubSettings, lambda resolver: settings)
    installation = ModelInstallation(
        str(store.root), str(store.data_dir), "eval", "/opt/model/llama-server", 18081
    )
    registry.provide(ModelInstallation, lambda resolver: installation)
    register(registry)
    resolver = registry.resolve()
    assert resolver.require(ModelReleaseReader).get("model_candidate").release_id == "model_candidate"
    assert resolver.require(ModelRuntimePort).probe(timeout_s=1).healthy is False


@contextmanager
def probe_server(path: str, context: int, adapters: list[object]) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.headers["Authorization"] == "Bearer probe_key"
            value: object = (
                {"status": "ok"}
                if self.path == "/health"
                else {"model_path": path, "default_generation_settings": {"n_ctx": context}}
                if self.path == "/props"
                else adapters
            )
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.005), daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.trace("R23", "T-E17-033")
@pytest.mark.parametrize("fault", ["none", "path", "context", "adapter", "hash"])
def test_identity_probe_uses_real_tcp_and_rejects_every_wrong_identity(
    fault: str, store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    if fault == "hash":
        path.chmod(0o644)
        path.write_bytes(b"changed")
        path.chmod(0o444)
    with probe_server(
        "/other/file.gguf" if fault == "path" else str(path),
        4 if fault == "context" else 32768,
        [{"id": 0}] if fault == "adapter" else [],
    ) as port:
        result = RuntimeIdentity(store, port=port, key="probe_key", max_bytes=65536).probe(
            "model_candidate", timeout_s=0.5
        )
    if fault == "none":
        assert result.healthy and result.identity_ok and result.adapters_empty
    else:
        assert not (result.healthy and result.identity_ok and result.adapters_empty)
        assert result.detail_code == "model_identity_mismatch"


@pytest.mark.trace("D25", "D17", "T-E17-043", "T-E17-080")
def test_stub_runs_through_canonical_registry_and_checks_pins(store: ManifestStore) -> None:
    from crew_health.model.adapters.manifest_store import durable_json
    from crew_health.model.domain.release import ModelRefused

    from .test_integration import Context
    from .test_transport import Cancel, TestClock, Tokens

    script = json.dumps(
        {
            "scenario": "demo",
            "entries": [
                {
                    "conversation_ordinal": 0,
                    "phase": "general",
                    "step_index": 0,
                    "final": {"kind": "stop"},
                    "tool_calls": [],
                    "thinking_canary": "PRIVATE_REASONING",
                }
            ],
        }
    ).encode()
    document = store.read_json(store.root / "releases/model_candidate.json")
    document["stub_script_sha256"] = hashlib.sha256(script).hexdigest()
    durable_json(store.root / "releases/model_candidate.json", document)
    registry = DeclarationRegistry(max_ports=32, max_hooks=128)
    settings = MemorySettings(
        specifications(),
        {
            "model.max_bytes": 1048576,
            "model.max_chunks": 256,
            "model.max_tools": 8,
            "model.connect_timeout_ms": 1000,
            "model.cancel_poll_ms": 10,
            "model.memory_budget_bytes": 6000000000,
            "model.cpu_budget": 3,
            "model.nice": 12,
            "model.stop_grace_ms": 1000,
        },
    )
    registry.provide(HubSettings, lambda r: settings)
    registry.provide(
        ModelInstallation,
        lambda r: ModelInstallation(str(store.root), str(store.data_dir), "eval", "/missing/server", 18081),
    )
    registry.provide(StubInstallation, lambda r: StubInstallation(script, "demo", 0, Tokens()))
    register(registry)
    resolver = registry.resolve()
    runtime = resolver.require(ModelRuntimePort)
    runtime.start("model_candidate")
    assert runtime.probe(timeout_s=1).detail_code == "stub"
    prompt = resolver.require(PromptPort).build("model_candidate", "general", Context({}), ())
    clock = TestClock()
    deadline = Deadline(clock.monotonic_ns() + 10**9, clock.monotonic_ns() + 10**9, clock)
    result = resolver.require(InferencePort).run_step(
        StepRequest("model_candidate", "general", prompt),
        deadline=deadline,
        cancel=Cancel(),
        progress=lambda value: None,
    )
    assert isinstance(result, StepOk) and result.final == {"kind": "stop"}
    assert "PRIVATE_REASONING" not in repr(result)
    from crew_health.model.module import ModelServices

    services = resolver.require(ModelServices)
    assert services.launch is not None
    assert services.launch.memory_budget_bytes == 6000000000
    assert services.launch.cpu_budget == 3
    assert services.launch.nice == 12
    document["decoding"]["temperature"] = 0.3  # type: ignore[index]
    durable_json(store.root / "releases/model_candidate.json", document)
    with pytest.raises(ModelRefused, match="release_changed"):
        resolver.require(PromptPort).build("model_candidate", "general", Context({}), ())
    runtime.stop()
    assert not runtime.probe(timeout_s=1).healthy
