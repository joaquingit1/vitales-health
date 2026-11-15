from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemorySettings
from crew_health.tools.adapters.cli_bench import (
    DevManifestReader,
    RetrievalLabel,
    benchmark_metrics,
    binary_metrics,
    no_answer_accuracy,
)
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.settings import setting_specs


@pytest.mark.trace("T-E12-080", "E12-02")
def test_metrics_have_independently_calculated_goldens() -> None:
    recall, ndcg, precision = binary_metrics(("a", "x", "b"), frozenset({"a", "b", "c"}), k=3, k_max=50)
    assert recall == Decimal(2) / Decimal(3)
    assert precision == Decimal(2) / Decimal(3)
    assert abs(ndcg - Decimal("0.7039180890341347")) < Decimal("0.000000000000001")


@pytest.mark.trace("T-E12-080", "E12-02")
def test_deduplication_preserves_first_rank() -> None:
    assert binary_metrics(("a", "a", "b"), frozenset({"b"}), k=2, k_max=50) == binary_metrics(
        ("a", "b"), frozenset({"b"}), k=2, k_max=50
    )
    assert binary_metrics((), frozenset({"a"}), k=2, k_max=50) == (Decimal(0), Decimal(0), Decimal(0))


@pytest.mark.trace("T-E12-080", "E12-02")
def test_empty_population_and_failed_no_answer_are_never_success() -> None:
    assert no_answer_accuracy((), max_queries=500) is None
    assert no_answer_accuracy(
        ("no_result", "unavailable", "error", "timeout", "not_run"), max_queries=500
    ) == Decimal("0.2")
    with pytest.raises(ToolsInvalid):
        binary_metrics((), frozenset(), k=2, k_max=50)


@pytest.mark.trace("T-E12-080", "T-E12-005", "I2")
@pytest.mark.parametrize("k", [0, -1, 51])
def test_metric_work_is_bounded(k: int) -> None:
    with pytest.raises(ToolsInvalid):
        binary_metrics(("a",), frozenset({"a"}), k=k, k_max=50)


# E18 owns eval/bench (FreezeLibrary, PR #36, not on main yet). Without it there is no owner verifier to run
# against, so these two cases are reported as blocked on E18 instead of passing against a local copy.
E18_BENCH = pytest.mark.skipif(
    not Path("eval/bench/pyproject.toml").is_file(), reason="blocked on E18: eval/bench is not on main yet"
)


def _owner_manifest_case(tmp_path: Path, kind: str) -> None:
    from crew_health_bench.domain.errors import BenchError
    from crew_health_bench.freeze import FreezeLibrary

    from crew_health.kernel.errors import StorageUnavailable
    from crew_health.kernel.testing import MemoryClock

    root = tmp_path / "eval/bench/cases/dev"
    root.mkdir(parents=True)
    public = root / "allowed.case.json"
    raw = b'{"case_id":"case_a"}'
    public.write_bytes(raw)
    hidden = tmp_path / "eval/vault/holdout.labels.json"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b'{"private_target":"canary"}')
    (root / "adjacent.labels.json").write_bytes(b"hidden target")
    values: dict[str, object] = {}
    for spec in setting_specs():
        assert spec.maximum is not None
        values[spec.key] = int(spec.maximum)
    settings = MemorySettings(setting_specs(), values)
    owner = FreezeLibrary(
        repository_root=tmp_path,
        clock=MemoryClock(datetime(2026, 1, 1, tzinfo=UTC), 0, "bench_epoch"),
        frozen_by="independent_eval",
        max_files=settings.integer("tools.bench.max_queries"),
        max_file_bytes=settings.integer("tools.max_schema_bytes"),
    )
    manifest = owner.freeze_tree(root, include=("allowed.case.json",), exclude=())
    reader = DevManifestReader(tmp_path, settings, verify_tree=owner.verify_tree)
    if kind == "valid":
        read = reader.load(manifest, profile="eval")
        assert len(read) == 1 and read[0].raw == raw
        assert read[0].path == "allowed.case.json"
        assert b"canary" not in read[0].raw
        return
    if kind == "symlink":
        public.unlink()
        public.symlink_to(hidden)
    elif kind == "hash":
        public.write_bytes(b"changed")
    elif kind != "profile":
        relative = {
            "traversal": "../../../vault/holdout.labels.json",
            "absolute": str(hidden),
            "adjacent": "adjacent.labels.json",
        }[kind]
        manifest = replace(manifest, files={relative: manifest.files["allowed.case.json"]})
    with pytest.raises((BenchError, ToolsInvalid, StorageUnavailable)):
        reader.load(manifest, profile="flight" if kind == "profile" else "eval")


def _run_owner_case(tmp_path: Path, kind: str) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "eval/bench",
            "--locked",
            "--offline",
            "python",
            str(Path(__file__).resolve()),
            str(tmp_path),
            kind,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


@E18_BENCH
@pytest.mark.trace("T-E12-042", "R23", "V33")
@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "adjacent", "hash", "profile"])
def test_dev_reader_denies_every_path_outside_verified_manifest(tmp_path: Path, kind: str) -> None:
    _run_owner_case(tmp_path, kind)


@E18_BENCH
@pytest.mark.trace("T-E12-042", "T-E12-080", "R23")
def test_dev_reader_reads_only_allowlisted_unchanged_file(tmp_path: Path) -> None:
    _run_owner_case(tmp_path, "valid")


@pytest.mark.trace("T-E12-080", "E12-02")
def test_failures_and_unrun_queries_stay_in_original_denominators() -> None:
    labels = (
        RetrievalLabel("a", frozenset({"p"}), False),
        RetrievalLabel("b", frozenset({"q"}), False),
        RetrievalLabel("c", frozenset(), True),
        RetrievalLabel("d", frozenset(), True),
    )
    result = benchmark_metrics(
        labels, {"a": ("ok", ("p",)), "c": ("no_result", ())}, k=1, max_queries=500, k_max=50
    )
    assert (result.recall, result.ndcg, result.support_precision, result.no_answer_accuracy) == (
        Decimal("0.5"),
    ) * 4
    assert result.failures == 2


@pytest.mark.trace("T-E12-080", "E12-02")
def test_empty_answerable_population_is_not_measured() -> None:
    result = benchmark_metrics(
        (RetrievalLabel("a", frozenset(), True),), {"a": ("error", ())}, k=1, max_queries=500, k_max=50
    )
    assert result.recall is result.ndcg is result.support_precision is None
    assert result.no_answer_accuracy == 0


if __name__ == "__main__":
    _owner_manifest_case(Path(sys.argv[1]), sys.argv[2])
