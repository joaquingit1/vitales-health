from __future__ import annotations

import os
import secrets
import time

from crew_health.model.adapters.launch import LaunchBuilder
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.runtime_probe import RuntimeIdentity
from crew_health.model.application.ports import (
    CancelToken,
    Deadline,
    ModelArtifactTrust,
    RuntimeProbe,
    SupervisorControl,
)
from crew_health.model.domain.release import ModelRefused


class RuntimeService:
    def __init__(
        self,
        store: ManifestStore,
        launch: LaunchBuilder,
        supervisor: SupervisorControl,
        *,
        port: int,
        max_bytes: int,
        trust: ModelArtifactTrust | None = None,
    ) -> None:
        self.store, self.launch, self.supervisor, self.port, self.max_bytes = (
            store,
            launch,
            supervisor,
            port,
            max_bytes,
        )
        self.trust = trust
        self.release_fingerprint: str | None = None
        self.release_id: str | None = None
        self.key = ""
        self.identity: RuntimeIdentity | None = None
        self.loading_until_ns = 0
        self.ready = False

    def start(self, release_id: str) -> None:
        if self.release_id is not None:
            raise ModelRefused("runtime_already_started")
        release = self.store.get(release_id)
        if self.store.profile == "flight":
            if release.document["status"] != "promoted":
                raise ModelRefused("not_promoted")
            if self.trust is None:
                raise ModelRefused("signed_artifact_verification_unavailable")
            self.trust.verify(release)
        if release.mode != "live":
            raise ModelRefused("stub_requires_explicit_script")
        spec = self.launch.launch_spec(release_id)
        attempted = False
        try:
            self.key = secrets.token_hex(32)
            self._write_key()
            self.release_fingerprint = self.store.fingerprint(release_id)
            self.release_id = release_id
            self.identity = RuntimeIdentity(
                self.store, port=self.port, key=self.key, max_bytes=self.max_bytes
            )
            self.loading_until_ns = time.monotonic_ns() + int(spec.ready_timeout_s * 1e9)
            self.ready = False
            attempted = True
            self.supervisor.enable("model", spec)
        except Exception:
            # A failure after enable may still have created the process. Cleanup must precede retry.
            if attempted:
                self.supervisor.disable("model")
            self._reset()
            raise

    def _write_key(self) -> None:
        path = self.store.data_dir / "models/runtime.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("runtime.key." + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self.key + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def stop(self) -> None:
        self.supervisor.disable("model")
        self._reset()

    def _reset(self) -> None:
        self.release_id = None
        self.release_fingerprint = None
        self.identity = None
        self.key = ""
        self.ready = False
        self.loading_until_ns = 0
        (self.store.data_dir / "models/runtime.key").unlink(missing_ok=True)

    def pins_valid(self) -> bool:
        return self.release_id is not None and self.release_fingerprint == self.store.fingerprint(
            self.release_id
        )

    def probe(
        self,
        *,
        timeout_s: float,
        deadline: Deadline | None = None,
        cancel: CancelToken | None = None,
        cancel_poll_s: float = 0.01,
    ) -> RuntimeProbe:
        if self.release_id is None or self.identity is None:
            return RuntimeProbe(False, False, False, "model_stopped")
        try:
            pins_valid = self.pins_valid()
        except ModelRefused, OSError:
            self.supervisor.disable("model")
            return RuntimeProbe(False, False, False, "model_configuration_invalid")
        if not pins_valid:
            self.supervisor.disable("model")
            return RuntimeProbe(False, False, False, "release_changed")
        if not self.ready:
            load_remaining = (self.loading_until_ns - time.monotonic_ns()) / 1e9
            if load_remaining <= 0:
                self.supervisor.disable("model")
                return RuntimeProbe(False, False, False, "model_load_timeout")
            timeout_s = min(timeout_s, load_remaining)
        result = self.identity.probe(
            self.release_id,
            timeout_s=timeout_s,
            deadline=deadline,
            cancel=cancel,
            cancel_poll_s=cancel_poll_s,
        )
        if result.healthy and result.identity_ok and result.adapters_empty:
            self.ready = True
            return result
        if not self.ready and time.monotonic_ns() >= self.loading_until_ns:
            self.supervisor.disable("model")
            return RuntimeProbe(False, False, False, "model_load_timeout")
        if result.detail_code == "model_identity_mismatch":
            self.supervisor.disable("model")
        elif result.detail_code in ("model_loading", "model_unavailable") and not self.ready:
            if time.monotonic_ns() >= self.loading_until_ns:
                self.supervisor.disable("model")
                return RuntimeProbe(False, False, False, "model_load_timeout")
            return RuntimeProbe(False, False, False, "model_loading")
        return result
