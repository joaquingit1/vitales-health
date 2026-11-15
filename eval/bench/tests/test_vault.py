"""T-E18-07: actual age encryption, key separation and bounded plaintext lifetime."""

import json
from pathlib import Path

import pytest
from pyrage import x25519

from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.adapters.vault import AgeVault
from crew_health_bench.domain.errors import BenchError

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "V33", "INV-E18-03", "T-E18-07")]


def test_encrypt_round_trip_and_locked_generator(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    root = repository / "vault"
    identity = x25519.Identity.generate()
    key = tmp_path / "identity"
    key.write_text(str(identity), encoding="utf-8")
    source = tmp_path / "case.json"
    data = {"rubric": ["synthetic hidden criterion"], "canary": "synthetic canary"}
    source.write_text(json.dumps(data), encoding="utf-8")
    with FileArtifactStore(root, max_line_bytes=4096, max_records=10) as store:
        vault = AgeVault(
            root=root,
            repository_root=repository,
            store=store,
            max_file_bytes=4096,
            max_files=10,
            environment={"CHC_EVAL_IDENTITY": str(key)},
            generation_epoch="generator",
            current_epoch="grader",
        )
        vault.seal((source,), (str(identity.to_public()),))
        first = (root / "holdout/case.age").read_bytes()
        assert b"synthetic hidden criterion" not in first
        assert vault.open_hidden("case") == data
        vault.seal((source,), (str(identity.to_public()),))
        assert (root / "holdout/case.age").read_bytes() == first
        locked = AgeVault(
            root=root,
            repository_root=repository,
            store=store,
            max_file_bytes=4096,
            max_files=10,
            environment={},
            generation_epoch="generator",
            current_epoch="generator",
        )
        with pytest.raises(BenchError, match="vault_locked"):
            locked.open_hidden("case")
        same_process = AgeVault(
            root=root,
            repository_root=repository,
            store=store,
            max_file_bytes=4096,
            max_files=10,
            environment={"CHC_EVAL_IDENTITY": str(key)},
            generation_epoch="generator",
            current_epoch="generator",
        )
        with pytest.raises(BenchError, match="vault_locked"):
            same_process.open_hidden("case")
        key.write_text(str(x25519.Identity.generate()), encoding="utf-8")
        with pytest.raises(BenchError, match="vault_locked"):
            vault.open_hidden("case")
        key.write_text(str(identity), encoding="utf-8")
        source.write_text('{"rubric":"changed"}', encoding="utf-8")
        with pytest.raises(BenchError, match="integrity"):
            vault.seal((source,), (str(identity.to_public()),))
        assert (root / "holdout/case.age").read_bytes() == first
        (root / "holdout/case.age").write_bytes(first[:-1] + bytes((first[-1] ^ 1,)))
        with pytest.raises(BenchError, match="integrity"):
            vault.open_hidden("case")


def test_missing_recipient_and_repository_plaintext_refused(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    with FileArtifactStore(root, max_line_bytes=4096, max_records=10) as store:
        vault = AgeVault(
            root=root,
            repository_root=tmp_path,
            store=store,
            max_file_bytes=4096,
            max_files=10,
            environment={},
            generation_epoch="generator",
            current_epoch="grader",
        )
        with pytest.raises(BenchError, match="recipient_missing"):
            vault.seal((), ())
        source = tmp_path / "case.json"
        source.write_text("{}", encoding="utf-8")
        with pytest.raises(BenchError, match="vault_path_escape"):
            vault.seal((source,), (str(x25519.Identity.generate().to_public()),))


def test_ciphertext_metadata_symlink_is_refused(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = tmp_path / "case.json"
    source.write_text("{}", encoding="utf-8")
    root = repository / "vault"
    with FileArtifactStore(root, max_line_bytes=4096, max_records=10) as files:
        vault = AgeVault(
            root=root,
            repository_root=repository,
            store=files,
            max_file_bytes=4096,
            max_files=10,
            environment={},
        )
        recipient = str(x25519.Identity.generate().to_public())
        vault.seal((source,), (recipient,))
        receipt = root / "holdout/case.age.json"
        outside = tmp_path / "receipt.json"
        receipt.rename(outside)
        receipt.symlink_to(outside)
        with pytest.raises(BenchError, match="vault_path_escape"):
            vault.verify("case")
