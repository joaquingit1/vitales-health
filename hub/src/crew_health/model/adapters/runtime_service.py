from __future__ import annotations

import os
import secrets

from crew_health.model.adapters.launch import LaunchBuilder
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.runtime_probe import RuntimeIdentity
from crew_health.model.application.ports import ModelArtifactTrust, RuntimeProbe, SupervisorControl
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
        self.key = secrets.token_hex(32)
        path = self.store.data_dir / "models/runtime.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace the per-start credential without following a preexisting symlink.
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
        self.release_fingerprint = self.store.fingerprint(release_id)
        self.release_id = release_id
        self.identity = RuntimeIdentity(self.store, port=self.port, key=self.key, max_bytes=self.max_bytes)
        self.supervisor.enable("model", spec)

    def stop(self) -> None:
        self.supervisor.disable("model")
        self.release_id = None
        self.release_fingerprint = None
        self.identity = None
        self.key = ""

    def pins_valid(self) -> bool:
        return self.release_id is not None and self.release_fingerprint == self.store.fingerprint(
            self.release_id
        )

    def probe(self, *, timeout_s: float) -> RuntimeProbe:
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
        result = self.identity.probe(self.release_id, timeout_s=timeout_s)
        if not result.identity_ok or not result.adapters_empty:
            self.supervisor.disable("model")
        return result
