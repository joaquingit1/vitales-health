from __future__ import annotations

import http.client
from pathlib import Path

from crew_health.model.adapters.manifest_store import ManifestStore, file_hash
from crew_health.model.application.ports import RuntimeProbe
from crew_health.model.domain.release import ModelRefused, object_value, strict_json


class RuntimeIdentity:
    def __init__(self, store: ManifestStore, *, port: int, key: str, max_bytes: int) -> None:
        self.store, self.port, self.key, self.max_bytes = store, port, key, max_bytes
        self.cache: dict[tuple[int, int, int, int], str] = {}

    def probe(self, release_id: str, *, timeout_s: float, force_hash: bool = False) -> RuntimeProbe:
        if not 0 < timeout_s <= 30:
            return RuntimeProbe(False, False, False, "probe_timeout_invalid")
        try:
            document = self.store.get(release_id).document
            weights = object_value(document["weights"])
            runtime = object_value(document["runtime"])
            path = self.store.data_dir / "models" / str(weights["file_name"])
            stat = path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if force_hash or signature not in self.cache:
                self.cache.clear()
                self.cache[signature] = file_hash(path)
            if (
                self.cache[signature] != weights["weights_sha256"]
                or path.is_symlink()
                or stat.st_mode & 0o222
            ):
                return RuntimeProbe(False, False, False, "model_identity_mismatch")
            health = object_value(self._read("/health", timeout_s))
            props = object_value(self._read("/props", timeout_s))
            adapters = self._read("/lora-adapters", timeout_s)
            settings = object_value(props.get("default_generation_settings", {}))
            identity = (
                Path(str(props.get("model_path", ""))).resolve() == path.resolve()
                and settings.get("n_ctx") == runtime["context_tokens"]
            )
            healthy = health.get("status") == "ok"
            empty = adapters == []
            return RuntimeProbe(
                healthy,
                identity,
                empty,
                "ok" if healthy and identity and empty else "model_identity_mismatch",
            )
        except OSError, http.client.HTTPException, ModelRefused, KeyError, TypeError, ValueError:
            return RuntimeProbe(False, False, False, "model_unavailable")

    def _read(self, path: str, timeout: float) -> object:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + self.key})
            response = connection.getresponse()
            if response.status != 200:
                raise ModelRefused("probe_http_failed")
            return strict_json(response.read(self.max_bytes + 1), max_bytes=self.max_bytes)
        finally:
            connection.close()
