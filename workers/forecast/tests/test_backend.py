import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_health_forecast.adapters.chronos_backend import ChronosBackend, weights_sha256
from crew_health_forecast.application.policy import ForecastPolicy


@pytest.mark.trace("E14-03", "V32", "V69")
def test_missing_and_unpinned_artifact(policy: ForecastPolicy, tmp_path: Path) -> None:
    assert ChronosBackend.load(None, policy).status().reason == "weights_absent"
    unpinned = replace(policy, model=policy.model.model_copy(update={"sha256": None}))
    assert ChronosBackend.load(tmp_path, unpinned).status().reason == "model_hash_unpinned"


@pytest.mark.trace("E14-03", "V32", "V69")
def test_hash_mismatch(policy: ForecastPolicy, tmp_path: Path) -> None:
    (tmp_path / "weights").write_bytes(b"local model file")
    pinned = replace(policy, model=policy.model.model_copy(update={"sha256": "0" * 64}))
    assert ChronosBackend.load(tmp_path, pinned).status().reason == "weights_hash_mismatch"
    assert ChronosBackend.load(tmp_path / "missing", pinned).status().reason == "weights_hash_mismatch"


@pytest.mark.trace("E14-03", "V32", "V69")
def test_package_absent_after_hash_verification(
    policy: ForecastPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "weights").write_bytes(b"local file")
    pinned = replace(policy, model=policy.model.model_copy(update={"sha256": weights_sha256(tmp_path)}))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert ChronosBackend.load(tmp_path, pinned).status().reason == "model_package_absent"


@pytest.mark.trace("E14-03", "V32")
def test_startup_load_once_and_failure_status(
    policy: ForecastPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "weights").write_bytes(b"local file")
    pinned = replace(policy, model=policy.model.model_copy(update={"sha256": weights_sha256(tmp_path)}))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    calls: list[tuple[str, str]] = []

    def load(path: str, *, device_map: str, local_files_only: bool) -> object:
        assert local_files_only is True
        calls.append((path, device_map))
        return object()

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(BaseChronosPipeline=SimpleNamespace(from_pretrained=load)),
    )
    backend = ChronosBackend.load(tmp_path, pinned)
    assert backend.status().ready
    assert backend.status().sha256 == pinned.model.sha256
    assert calls == [(str(tmp_path), "cpu")]
    backend.status()
    assert len(calls) == 1

    def failed(path: str, *, device_map: str, local_files_only: bool) -> object:
        raise RuntimeError("load failed")

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(BaseChronosPipeline=SimpleNamespace(from_pretrained=failed)),
    )
    assert ChronosBackend.load(tmp_path, pinned).status().reason == "model_load_failed"


@pytest.mark.trace("E14-03", "V32")
def test_hash_is_order_independent_but_covers_names_and_bytes(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    for name, data in (("b", b"bbb"), ("a", b"aaa")):
        (left / name).write_bytes(data)
    for name, data in (("a", b"aaa"), ("b", b"bbb")):
        (right / name).write_bytes(data)
    original = weights_sha256(left)
    assert original == weights_sha256(right)
    (right / "a").write_bytes(b"changed")
    assert original != weights_sha256(right)
    (right / "a").write_bytes(b"aaa")
    (right / "a").rename(right / "renamed")
    assert original != weights_sha256(right)


@pytest.mark.trace("E14-03", "V69")
def test_weight_file_count_limit(tmp_path: Path) -> None:
    for index in range(64):
        (tmp_path / str(index)).write_bytes(b"x")
    assert len(weights_sha256(tmp_path)) == 64
    (tmp_path / "overflow").write_bytes(b"x")
    with pytest.raises(ValueError, match="weights_file_limit"):
        weights_sha256(tmp_path)
