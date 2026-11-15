"""Offline-only optional model adapter. No model dependency is imported at module load."""

import hashlib
import importlib
import importlib.util
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from crew_health_forecast.application.policy import ForecastPolicy
from crew_health_forecast.application.ports import BackendStatus


class BackendUnavailable(RuntimeError):
    pass


def weights_sha256(directory: Path) -> str:
    """Hash framed relative names and file bytes in sorted order, with at most 64 files."""
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("invalid_weights_directory")
    files: list[Path] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("weights_symlink")
        if path.is_file():
            files.append(path)
            if len(files) > 64:
                raise ValueError("weights_file_limit")
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(directory).as_posix()):
        name = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
    return digest.hexdigest()


class ChronosBackend:
    def __init__(self, status: BackendStatus) -> None:
        self._status = status
        self._pipeline: Any = None

    @classmethod
    def load(cls, weights_dir: Path | None, policy: ForecastPolicy) -> ChronosBackend:
        def unavailable(reason: str) -> ChronosBackend:
            return cls(
                BackendStatus(False, reason, policy.model.id, policy.model.revision, policy.model.sha256)
            )

        if weights_dir is None:
            return unavailable("weights_absent")
        if policy.model.sha256 is None:
            return unavailable("model_hash_unpinned")
        try:
            actual = weights_sha256(weights_dir)
        except OSError, ValueError:
            return unavailable("weights_hash_mismatch")
        if actual != policy.model.sha256.lower():
            return unavailable("weights_hash_mismatch")
        if importlib.util.find_spec("chronos") is None:
            return unavailable("model_package_absent")
        try:
            chronos = importlib.import_module("chronos")
            pipeline = chronos.BaseChronosPipeline.from_pretrained(
                str(weights_dir.resolve()), device_map="cpu", local_files_only=True
            )
        except Exception:
            return unavailable("model_load_failed")
        backend = cls(BackendStatus(True, None, policy.model.id, policy.model.revision, actual))
        backend._pipeline = pipeline
        return backend

    def status(self) -> BackendStatus:
        return self._status

    def predict(
        self, context: Sequence[Decimal], horizon: int, low: Decimal, high: Decimal
    ) -> list[tuple[Decimal, Decimal, Decimal]]:
        # Real inference is exercised by tests/test_chronos_inference.py (slow, needs the model extra and weights).
        if not self._status.ready or self._pipeline is None:
            raise BackendUnavailable(self._status.reason)
        return _quantiles(self._pipeline, context, horizon, low, high)


def _quantiles(  # pragma: no cover
    pipeline: Any, context: Sequence[Decimal], horizon: int, low: Decimal, high: Decimal
) -> list[tuple[Decimal, Decimal, Decimal]]:
    torch = importlib.import_module("torch")
    # A list holding one univariate 1-d series is accepted by every Chronos pipeline. Chronos-2 returns one
    # (variates, horizon, quantiles) tensor per series; Bolt and T5 return one (batch, horizon, quantiles) tensor.
    quantiles, _ = pipeline.predict_quantiles(
        [torch.tensor([float(value) for value in context])],
        prediction_length=horizon,
        quantile_levels=[float(low), 0.5, float(high)],
    )
    series = quantiles[0]
    if series.dim() == 3:
        series = series[0]
    if tuple(series.shape) != (horizon, 3):
        raise BackendUnavailable("model_output_invalid")
    return [
        (
            Decimal(repr(float(series[index, 0]))),
            Decimal(repr(float(series[index, 1]))),
            Decimal(repr(float(series[index, 2]))),
        )
        for index in range(horizon)
    ]
