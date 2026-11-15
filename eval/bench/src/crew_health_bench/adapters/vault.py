from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from pyrage import DecryptError, EncryptError, IdentityError, RecipientError, decrypt, encrypt, x25519

from crew_health_bench.adapters.documents import decode_json, read_json
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.application.ports import AtomicArtifacts
from crew_health_bench.domain.documents import text, text_list
from crew_health_bench.domain.errors import BenchError


class AgeVault:
    def __init__(
        self,
        *,
        root: Path,
        repository_root: Path,
        store: AtomicArtifacts,
        max_file_bytes: int,
        max_files: int,
        environment: Mapping[str, str],
        generation_epoch: str | None = None,
        current_epoch: str | None = None,
    ) -> None:
        if max_file_bytes < 1 or max_files < 1:
            raise BenchError("case_invalid")
        self.root, self.repository_root, self.store = root, repository_root.resolve(), store
        self.max_file_bytes, self.max_files = max_file_bytes, max_files
        self.environment = dict(environment)
        self.generation_epoch, self.current_epoch = generation_epoch, current_epoch

    def _read(self, path: Path) -> bytes:
        vault_path(path.parent, path.name)
        try:
            if not path.is_file():
                raise BenchError("target_unreadable", exit_code=6)
            with path.open("rb") as source:
                data = source.read(self.max_file_bytes + 1)
            if len(data) > self.max_file_bytes:
                raise BenchError("case_invalid")
            return data
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error

    def _cipher(self, case_id: str) -> Path:
        require_id(case_id)
        return vault_path(self.root, "holdout/" + case_id + ".age")

    def verify(self, case_id: str) -> Mapping[str, object]:
        path = self._cipher(case_id)
        metadata = vault_path(self.root, "holdout/" + case_id + ".age.json")
        receipt = read_json(metadata, max_file_bytes=self.max_file_bytes)
        if (
            receipt.keys() != {"hidden_sha256", "ciphertext_sha256", "recipients"}
            or sha256(self._read(path)).hexdigest() != receipt["ciphertext_sha256"]
        ):
            raise BenchError("integrity", exit_code=6)
        return receipt

    def seal(self, files: Sequence[Path], recipients: Sequence[str]) -> None:
        if not recipients:
            raise BenchError("recipient_missing", exit_code=5)
        if not files or len(files) > self.max_files or len(recipients) > self.max_files:
            raise BenchError("case_invalid")
        if len({path.stem for path in files}) != len(files):
            raise BenchError("case_invalid")
        try:
            keys = [x25519.Recipient.from_str(item) for item in recipients]
            for source in files:
                if source.resolve().is_relative_to(self.repository_root):
                    raise BenchError("vault_path_escape", exit_code=5)
                data = self._read(source)
                decode_json(data)
                target = self._cipher(source.stem)
                digest = sha256(data).hexdigest()
                metadata = vault_path(self.root, "holdout/" + source.stem + ".age.json")
                if metadata.exists():
                    receipt = self.verify(source.stem)
                    if receipt["hidden_sha256"] != digest or text_list(receipt["recipients"]) != tuple(
                        recipients
                    ):
                        raise BenchError("integrity", exit_code=6)
                    continue
                ciphertext = encrypt(data, keys)
                if len(ciphertext) > self.max_file_bytes:
                    raise BenchError("case_invalid")
                receipt = {
                    "hidden_sha256": digest,
                    "ciphertext_sha256": sha256(ciphertext).hexdigest(),
                    "recipients": list(recipients),
                }
                self.store.write_atomic(target.relative_to(self.root), ciphertext)
                self.store.write_atomic(
                    Path(str(target.relative_to(self.root)) + ".json"), canonical_json(receipt)
                )
        except RecipientError as error:
            raise BenchError("recipient_missing", exit_code=5) from error
        except EncryptError as error:
            raise BenchError("vault_locked", exit_code=5) from error

    def open_hidden(self, case_id: str) -> Mapping[str, object]:
        identity_path = self.environment.get("CHC_EVAL_IDENTITY")
        if (
            not identity_path
            or not self.generation_epoch
            or not self.current_epoch
            or self.generation_epoch == self.current_epoch
        ):
            raise BenchError("vault_locked", exit_code=5)
        receipt = self.verify(case_id)
        try:
            identity = x25519.Identity.from_str(self._read(Path(identity_path)).decode("utf-8").strip())
            plaintext = decrypt(self._read(self._cipher(case_id)), [identity])
            if sha256(plaintext).hexdigest() != text(receipt["hidden_sha256"]):
                raise BenchError("integrity", exit_code=6)
            with TemporaryDirectory(prefix="chc-bench-grade-") as directory:
                path = Path(directory) / (case_id + ".json")
                path.write_bytes(plaintext)
                return read_json(path, max_file_bytes=self.max_file_bytes)
        except (IdentityError, DecryptError, UnicodeError) as error:
            raise BenchError("vault_locked", exit_code=5) from error
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
