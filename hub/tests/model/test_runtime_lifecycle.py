from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest

from crew_health.model.adapters.launch import LaunchBuilder
from crew_health.model.adapters.llama_client import ClientLimits
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.runtime_service import RuntimeService
from crew_health.model.application.ports import (
    CancelToken,
    Deadline,
    LaunchSpec,
    RuntimeProbe,
    StepCancelled,
    StepTimedOut,
    StepUnavailable,
)
from crew_health.model.module import ModelServices

from .test_integration import live
from .test_transport import Cancel, TestClock, request


class Supervisor:
    def __init__(self, *, failure: str = "none") -> None:
        self.failure = failure
        self.running = False
        self.disabled: list[str] = []
        self.enabled = 0

    def enable(self, process_id: str, spec: LaunchSpec) -> None:
        self.enabled += 1
        if self.failure == "before":
            self.failure = "none"
            raise OSError("injected start failure before side effect")
        self.running = True
        if self.failure == "after":
            self.failure = "none"
            raise OSError("injected start failure after side effect")

    def disable(self, process_id: str) -> None:
        self.disabled.append(process_id)
        self.running = False

    def state(self, process_id: str) -> str:
        return "running" if self.running else "stopped"


class CountedRuntime(RuntimeService):
    probes = 0

    def probe(
        self,
        *,
        timeout_s: float,
        deadline: Deadline | None = None,
        cancel: CancelToken | None = None,
        cancel_poll_s: float = 0.01,
    ) -> RuntimeProbe:
        self.probes += 1
        return super().probe(
            timeout_s=timeout_s, deadline=deadline, cancel=cancel, cancel_poll_s=cancel_poll_s
        )


class Launch:
    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout

    def launch_spec(self, release_id: str) -> LaunchSpec:
        return LaunchSpec(
            "model",
            ("fake_at_supervisor_boundary",),
            {},
            "/tmp",
            "http://127.0.0.1/health",
            self.timeout,
            1000,
            1,
            0,
        )


@contextmanager
def identity_server(path: Path, state: dict[str, object]) -> Iterator[int]:
    import json

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(float(cast(float, state.get("delay", 0))))
            loading = self.path == "/health" and state.get("loading", False)
            raw = json.dumps(
                {"status": "loading model" if loading else "ok"}
                if self.path == "/health"
                else {
                    "model_path": "/wrong/path" if state.get("wrong", False) else str(path),
                    "default_generation_settings": {"n_ctx": 32768},
                }
                if self.path == "/props"
                else []
            ).encode()
            self.send_response(503 if loading else 200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except BrokenPipeError:
                pass

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


@pytest.mark.trace("R23", "T-E17-033", "T-E17-080")
def test_cold_loading_then_healthy_then_proven_wrong_identity(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    state: dict[str, object] = {"loading": True}
    supervisor = Supervisor()
    with identity_server(path, state) as port:
        runtime = RuntimeService(
            store, cast(LaunchBuilder, Launch()), supervisor, port=port, max_bytes=1048576
        )
        runtime.start("model_candidate")
        loading = runtime.probe(timeout_s=0.5)
        assert not loading.healthy and loading.detail_code == "model_loading"
        assert supervisor.disabled == []
        state["loading"] = False
        healthy = runtime.probe(timeout_s=0.5)
        assert healthy.healthy and healthy.identity_ok and healthy.adapters_empty
        assert supervisor.disabled == []
        state["wrong"] = True
        wrong = runtime.probe(timeout_s=0.5)
        assert wrong.detail_code == "model_identity_mismatch" and not wrong.identity_ok
        assert supervisor.disabled == ["model"]


@pytest.mark.trace("R23", "T-E17-080")
@pytest.mark.parametrize("failure", ["before", "after"])
def test_failed_start_cleans_partial_process_and_key_then_can_retry(
    failure: str, store: ManifestStore, manifest: dict[str, Any]
) -> None:
    live(store, manifest)
    supervisor = Supervisor(failure=failure)
    runtime = RuntimeService(store, cast(LaunchBuilder, Launch()), supervisor, port=18081, max_bytes=1048576)
    with pytest.raises(OSError):
        runtime.start("model_candidate")
    assert runtime.release_id is None and runtime.identity is None and not runtime.key
    assert not (store.data_dir / "models/runtime.key").exists()
    assert supervisor.disabled == ["model"] and not supervisor.running
    runtime.start("model_candidate")
    assert runtime.release_id == "model_candidate" and supervisor.running
    key = store.data_dir / "models/runtime.key"
    assert key.stat().st_mode & 0o777 == 0o600
    runtime.stop()
    assert not key.exists()


@pytest.mark.trace("V27", "T-E17-022")
@pytest.mark.parametrize(
    "kind", ["cancelled", "total", "first_token", "mid_probe_cancel", "mid_probe_timeout"]
)
def test_step_preflight_honors_cancel_and_elapsed_deadlines(
    kind: str, store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    supervisor = Supervisor()
    state: dict[str, object] = {"delay": 0.5}
    with identity_server(path, state) as port:
        runtime = CountedRuntime(
            store, cast(LaunchBuilder, Launch()), supervisor, port=port, max_bytes=1048576
        )
        runtime.start("model_candidate")
        services = ModelServices()
        services.runtime = runtime
        services.limits = ClientLimits(65536, 256, 8, 1, 0.01)
        prepared = replace(request(), release_id="model_candidate")
        clock, cancel = TestClock(), Cancel()
        now = clock.monotonic_ns()
        deadline = Deadline(
            now + (0 if kind == "total" else 2 * 10**9),
            now + (0 if kind == "first_token" else 8 * 10**7 if kind == "mid_probe_timeout" else 10**9),
            clock,
        )
        timer = threading.Timer(0.04, cancel.event.set)
        if kind == "cancelled":
            cancel.event.set()
        if kind == "mid_probe_cancel":
            timer.start()
        try:
            started = time.monotonic()
            result = services.run_step(
                prepared, deadline=deadline, cancel=cancel, progress=lambda value: None
            )
            elapsed = time.monotonic() - started
        finally:
            timer.cancel()
            runtime.stop()
        assert elapsed < 0.25
        if kind in ("cancelled", "total", "first_token"):
            assert runtime.probes == 0
        if kind in ("cancelled", "mid_probe_cancel"):
            assert isinstance(result, StepCancelled)
        else:
            assert result == StepTimedOut("total" if kind == "total" else "first_token")


@pytest.mark.trace("R23", "T-E17-080")
def test_cold_load_still_has_a_finite_manifest_deadline(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    supervisor = Supervisor()
    with identity_server(path, {"loading": True}) as port:
        runtime = RuntimeService(
            store, cast(LaunchBuilder, Launch(timeout=0.000001)), supervisor, port=port, max_bytes=1048576
        )
        runtime.start("model_candidate")
        result = runtime.probe(timeout_s=0.1)
        assert result.detail_code == "model_load_timeout"
        assert supervisor.disabled == ["model"]


@pytest.mark.trace("R23", "T-E17-080")
def test_unresponsive_health_does_not_extend_the_load_deadline(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    supervisor = Supervisor()
    with identity_server(path, {"loading": True, "delay": 0.5}) as port:
        runtime = RuntimeService(
            store, cast(LaunchBuilder, Launch(timeout=0.06)), supervisor, port=port, max_bytes=1048576
        )
        runtime.start("model_candidate")
        began = time.monotonic()
        result = runtime.probe(timeout_s=1)
        assert time.monotonic() - began < 0.25
        assert result.detail_code == "model_load_timeout"
        assert supervisor.disabled == ["model"]


@pytest.mark.trace("R17", "T-E17-020", "T-E17-033")
def test_healthy_non_cancelled_request_reaches_real_inference_transport(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    import hashlib

    from crew_health.model.application.prompt_builder import request_bytes

    path = live(store, manifest)
    supervisor = Supervisor()
    with identity_server(path, {}) as port:
        runtime = RuntimeService(
            store, cast(LaunchBuilder, Launch()), supervisor, port=port, max_bytes=1048576
        )
        runtime.start("model_candidate")
        services = ModelServices()
        services.runtime = runtime
        services.limits = ClientLimits(65536, 256, 8, 1, 0.01)
        prepared = replace(request(), release_id="model_candidate")
        prepared = replace(
            prepared,
            bundle=replace(
                prepared.bundle,
                prompt_sha256=hashlib.sha256(request_bytes(prepared.bundle, prepared.release_id)).hexdigest(),
            ),
        )
        clock = TestClock()
        now = clock.monotonic_ns()
        result = services.run_step(
            prepared,
            deadline=Deadline(now + 10**9, now + 10**9, clock),
            cancel=Cancel(),
            progress=lambda value: None,
        )
        # This server implements identity endpoints only; reaching its real POST produces HTTP501.
        assert result == StepUnavailable("model_http_error")
        assert runtime.ready and supervisor.disabled == []
        runtime.stop()
