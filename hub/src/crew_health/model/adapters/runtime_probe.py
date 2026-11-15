from __future__ import annotations

import hashlib
import http.client
import queue
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.application.ports import CancelToken, Deadline, RuntimeProbe
from crew_health.model.domain.release import ModelRefused, object_value, strict_json

type FileStamp = tuple[int, int, int, int, int]


class HashState(Protocol):
    def update(self, value: bytes) -> None: ...
    def hexdigest(self) -> str: ...


@dataclass(slots=True)
class HashProgress:
    stamp: FileStamp
    digest: HashState
    offset: int = 0


class RuntimeIdentity:
    def __init__(self, store: ManifestStore, *, port: int, key: str, max_bytes: int) -> None:
        self.store, self.port, self.key, self.max_bytes = store, port, key, max_bytes
        self.cache: dict[FileStamp, str] = {}
        self.partial: HashProgress | None = None
        self.hash_lock = threading.Lock()

    def probe(
        self,
        release_id: str,
        *,
        timeout_s: float,
        force_hash: bool = False,
        deadline: Deadline | None = None,
        cancel: CancelToken | None = None,
        cancel_poll_s: float = 0.01,
    ) -> RuntimeProbe:
        if not 0 < timeout_s <= 30 or not 0 < cancel_poll_s <= 0.2:
            return RuntimeProbe(False, False, False, "probe_timeout_invalid")
        clock = deadline.clock.monotonic_ns if deadline is not None else time.monotonic_ns
        end = clock() + int(timeout_s * 1e9)
        if deadline is not None:
            end = min(end, deadline.total_ns, deadline.first_token_ns)
        stopped = threading.Event()
        sockets: list[socket.socket] = []
        results: queue.Queue[RuntimeProbe] = queue.Queue(maxsize=1)

        def remaining() -> float:
            value = (end - clock()) / 1e9
            if stopped.is_set() or value <= 0:
                raise ModelRefused("probe_stopped")
            return value

        def run() -> None:
            result = self._probe(release_id, force_hash=force_hash, remaining=remaining, sockets=sockets)
            try:
                results.put_nowait(result)
            except queue.Full:
                pass

        if cancel is not None and cancel.is_cancelled():
            return RuntimeProbe(False, False, False, "probe_cancelled")
        if clock() >= end:
            return RuntimeProbe(False, False, False, "probe_deadline")
        thread = threading.Thread(target=run, name="model-probe", daemon=True)
        thread.start()
        try:
            iterations = max(1, int((end - clock()) / 1e9 / cancel_poll_s) + 2)
            for _ in range(iterations):
                if cancel is not None and cancel.is_cancelled():
                    return RuntimeProbe(False, False, False, "probe_cancelled")
                available = (end - clock()) / 1e9
                if available <= 0:
                    return RuntimeProbe(False, False, False, "probe_deadline")
                try:
                    return results.get(timeout=min(cancel_poll_s, available))
                except queue.Empty:
                    pass
            return RuntimeProbe(False, False, False, "probe_deadline")
        finally:
            stopped.set()
            for connection in tuple(sockets):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            thread.join(timeout=cancel_poll_s)

    def _probe(
        self,
        release_id: str,
        *,
        force_hash: bool,
        remaining: Callable[[], float],
        sockets: list[socket.socket],
    ) -> RuntimeProbe:
        try:
            document = self.store.get(release_id).document
            weights = object_value(document["weights"])
            runtime = object_value(document["runtime"])
            path = self.store.data_dir / "models" / str(weights["file_name"])
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            digest = self._hash(path, signature, remaining, force_hash=force_hash)
            if digest != weights["weights_sha256"] or path.is_symlink() or stat.st_mode & 0o222:
                return RuntimeProbe(False, False, False, "model_identity_mismatch")
            health = object_value(self._read("/health", remaining, sockets))
            props = object_value(self._read("/props", remaining, sockets))
            adapters = self._read("/lora-adapters", remaining, sockets)
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
        except ModelRefused as error:
            return RuntimeProbe(
                False, False, False, "model_loading" if str(error) == "model_loading" else "model_unavailable"
            )
        except OSError, http.client.HTTPException, KeyError, TypeError, ValueError:
            return RuntimeProbe(False, False, False, "model_unavailable")

    @staticmethod
    def _stamp(path: Path) -> FileStamp:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def _hash(self, path: Path, stamp: FileStamp, remaining: Callable[[], float], *, force_hash: bool) -> str:
        # A cancelled probe leaves only bounded public-weight digest progress, never an accepted identity.
        while not self.hash_lock.acquire(timeout=min(remaining(), 0.01)):
            remaining()
        try:
            if force_hash or self.partial is not None and self.partial.stamp != stamp:
                self.partial = None
                self.cache.clear()
            if stamp in self.cache:
                if self._stamp(path) != stamp:
                    self.partial = None
                    self.cache.clear()
                    raise ModelRefused("weight_file_changed")
                return self.cache[stamp]
            if self.partial is None:
                self.cache.clear()
                self.partial = HashProgress(stamp, hashlib.sha256())
            progress = self.partial
            with path.open("rb") as stream:
                stream.seek(progress.offset)
                while progress.offset < stamp[2]:
                    remaining()
                    block = stream.read(min(1024 * 1024, stamp[2] - progress.offset))
                    if not block:
                        self.partial = None
                        raise ModelRefused("weight_file_changed")
                    progress.digest.update(block)
                    progress.offset += len(block)
            if self._stamp(path) != stamp:
                self.partial = None
                self.cache.clear()
                raise ModelRefused("weight_file_changed")
            digest = progress.digest.hexdigest()
            self.cache[stamp] = digest
            self.partial = None
            return digest
        finally:
            self.hash_lock.release()

    def _read(self, path: str, remaining: Callable[[], float], sockets: list[socket.socket]) -> object:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=remaining())
        try:
            connection.connect()
            if connection.sock is not None:
                sockets.append(connection.sock)
            remaining()
            connection.request("GET", path, headers={"Authorization": "Bearer " + self.key})
            response = connection.getresponse()
            if response.status == 503 and path == "/health":
                raise ModelRefused("model_loading")
            if response.status != 200:
                raise ModelRefused("probe_http_failed")
            return strict_json(response.read(self.max_bytes + 1), max_bytes=self.max_bytes)
        finally:
            connection.close()
