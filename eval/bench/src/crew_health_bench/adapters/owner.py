from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from pyrage import DecryptError, IdentityError, decrypt, x25519

from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.domain.errors import BenchError


class AgeOwnerGrant:
    def __init__(self, *, recipients: Sequence[str], environment: Mapping[str, str], max_file_bytes: int) -> None:
        self._identity: x25519.Identity | None = None
        self.maximum = max_file_bytes
        path = environment.get("CHC_EVAL_IDENTITY")
        if not path or not recipients:
            return
        try:
            identity = x25519.Identity.from_str(self._read(Path(path)).decode("utf-8").strip())
        except (IdentityError, UnicodeError) as error:
            raise BenchError("holdout_key_required", exit_code=5) from error
        if str(identity.to_public()) not in recipients:
            raise BenchError("holdout_key_required", exit_code=5)
        self._identity = identity

    def _read(self, path: Path) -> bytes:
        vault_path(path.parent, path.name)
        try:
            if not path.is_file():
                raise BenchError("target_unreadable", exit_code=6)
            with path.open("rb") as stream:
                raw = stream.read(self.maximum + 1)
            if len(raw) > self.maximum:
                raise BenchError("case_invalid")
            return raw
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error

    def require_owner(self) -> None:
        if self._identity is None:
            raise BenchError("holdout_key_required", exit_code=5)

    def salt(self, path: Path, *, repository: Path) -> bytes:
        self.require_owner()
        raw = self._read(path)
        if path.suffix == ".age":
            identity = self._identity
            if identity is None:
                raise BenchError("holdout_key_required", exit_code=5)
            try:
                raw = decrypt(raw, [identity])
            except DecryptError as error:
                raise BenchError("vault_locked", exit_code=5) from error
        elif path.resolve().is_relative_to(repository.resolve()):
            raise BenchError("vault_path_escape", exit_code=5)
        if len(raw) != 32:
            raise BenchError("salt_missing")
        return raw
