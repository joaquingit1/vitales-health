from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    if sys.platform == "win32":
        assert_windows_key_acl(key)
    else:
        assert key.stat().st_mode & 0o777 == 0o600
    runtime.stop()
    assert not key.exists()


def assert_windows_key_acl(key: Path) -> None:
    """Independently inspect the actual file via .NET ACLs, not the implementation's ctypes calls."""
    shell = shutil.which("powershell.exe")
    assert shell is not None
    script = (
        "$ErrorActionPreference='Stop'; "
        "$a=Get-Acl -LiteralPath $env:VITALES_KEY_ACL_TEST_PATH; "
        "$r=@($a.Access | ForEach-Object { @{"
        "sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
        "rights=[int]$_.FileSystemRights; type=$_.AccessControlType.ToString(); inherited=$_.IsInherited} }); "
        "@{protected=$a.AreAccessRulesProtected; rules=$r} | ConvertTo-Json -Depth 4 -Compress"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "VITALES_KEY_ACL_TEST_PATH": str(key)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    value = json.loads(result.stdout)
    assert value["protected"] is True
    rules = value["rules"]
    assert len(rules) == 2
    assert {rule["sid"] for rule in rules} == {"S-1-3-4", "S-1-5-18"}
    assert all(rule["type"] == "Allow" and rule["inherited"] is False for rule in rules)
    assert all(rule["rights"] == 0x1F01FF for rule in rules)


@pytest.mark.trace("R23", "T-E17-080")
@pytest.mark.parametrize("stage", ["before", "after_empty_creation"])
def test_key_permission_failure_writes_no_secret_and_leaves_no_file(
    stage: str, store: ManifestStore, manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from crew_health.model.adapters import runtime_service
    from crew_health.model.domain.release import ModelRefused

    live(store, manifest)
    supervisor = Supervisor()
    runtime = RuntimeService(store, cast(LaunchBuilder, Launch()), supervisor, port=18081, max_bytes=1048576)
    inspected: list[Path] = []

    def refused(path: Path) -> int:
        assert not path.exists()
        if stage == "after_empty_creation":
            path.write_bytes(b"")
        inspected.append(path)
        raise ModelRefused("runtime_key_permissions")

    monkeypatch.setattr(runtime_service, "create_runtime_key_file", refused)
    with pytest.raises(ModelRefused, match="runtime_key_permissions"):
        runtime.start("model_candidate")
    assert len(inspected) == 1
    assert supervisor.enabled == 0
    assert runtime.release_id is None and runtime.identity is None and not runtime.key
    assert list((store.data_dir / "models").glob("runtime.key*")) == []


@pytest.mark.trace("R23", "T-E17-080")
def test_runtime_key_creation_is_private_before_any_credential_bytes(tmp_path: Path) -> None:
    from crew_health.model.adapters.key_permissions import create_runtime_key_file
    from crew_health.model.domain.release import ModelRefused

    key = tmp_path / "empty-runtime.key"
    fd = create_runtime_key_file(key)
    try:
        assert key.read_bytes() == b""
        if sys.platform == "win32":
            assert_windows_key_acl(key)
        else:
            assert key.stat().st_mode & 0o777 == 0o600
        collision = ModelRefused if sys.platform == "win32" else FileExistsError
        with pytest.raises(collision):
            create_runtime_key_file(key)
    finally:
        os.close(fd)
        key.unlink()


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


@pytest.mark.trace("R23", "T-E17-033", "T-E17-080")
def test_short_probe_budgets_make_hash_progress_and_force_rechecks(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    import hashlib

    from crew_health.model.adapters.manifest_store import durable_json
    from crew_health.model.adapters.runtime_probe import RuntimeIdentity
    from crew_health.model.domain.release import ModelRefused

    from .test_composition import probe_server

    live(store, manifest)
    payload = b"EXPLICIT_HASH_PROGRESS_FIXTURE" * 310000
    digest = hashlib.sha256(payload).hexdigest()
    path = store.data_dir / "models" / (digest + ".gguf")
    path.write_bytes(payload)
    path.chmod(0o444)
    document = store.read_json(store.root / "releases/model_candidate.json")
    weights = document["weights"]
    assert isinstance(weights, dict)
    weights.update(file_name=path.name, weights_sha256=digest, size_bytes=len(payload))
    durable_json(store.root / "releases/model_candidate.json", document)
    with probe_server(str(path), 32768, []) as port:
        identity = RuntimeIdentity(store, port=port, key="probe_key", max_bytes=65536)

        def short_probe(*, force: bool = False) -> object:
            calls = 0

            def remaining() -> float:
                nonlocal calls
                calls += 1
                if calls >= 3:
                    raise ModelRefused("probe_stopped")
                return 1

            return identity._probe("model_candidate", force_hash=force, remaining=remaining, sockets=[])

        for _ in range(20):
            short_probe()
            if identity.cache:
                break
        assert identity.cache, "A full digest must eventually complete across bounded probe slices"
        assert next(iter(identity.cache.values())) == digest
        assert identity.probe("model_candidate", timeout_s=1).identity_ok
        short_probe(force=True)
        assert not identity.cache, "Force verification must not reuse a previously cached full digest"
        # Change the file while the new verification is partial. The old digest cannot be accepted.
        path.chmod(0o644)
        path.write_bytes(b"X" * len(payload))
        path.chmod(0o444)
        for _ in range(20):
            short_probe()
            if identity.cache:
                break
        assert identity.cache and next(iter(identity.cache.values())) != digest
        assert not identity.probe("model_candidate", timeout_s=1).identity_ok


@pytest.mark.trace("R23", "V27", "T-E17-033", "T-E17-022")
def test_cancelled_hash_probe_serializes_with_concurrent_probe(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    from crew_health.model.adapters.runtime_probe import RuntimeIdentity
    from crew_health.model.domain.release import ModelRefused

    from .test_composition import probe_server

    path = live(store, manifest)
    entered, released = threading.Event(), threading.Event()
    with probe_server(str(path), 32768, []) as port:
        identity = RuntimeIdentity(store, port=port, key="probe_key", max_bytes=65536)
        results: list[RuntimeProbe] = []
        calls = 0

        def cancelled_budget() -> float:
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
                assert released.wait(1)
                raise ModelRefused("probe_stopped")
            return 1

        first = threading.Thread(
            target=lambda: results.append(
                identity._probe("model_candidate", force_hash=False, remaining=cancelled_budget, sockets=[])
            )
        )
        second = threading.Thread(
            target=lambda: results.append(
                identity._probe("model_candidate", force_hash=False, remaining=lambda: 1, sockets=[])
            )
        )
        first.start()
        assert entered.wait(1)
        second.start()
        released.set()
        first.join(timeout=1)
        second.join(timeout=1)
        assert not first.is_alive() and not second.is_alive()
        assert len(results) == 2 and sum(r.healthy and r.identity_ok for r in results) == 1
        assert identity.partial is None and len(identity.cache) == 1
