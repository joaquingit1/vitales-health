"""Fetch the pinned MedQA file into the ignored run directory and load it after a hash check."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import httpx

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.medqa.config import DatasetPin
from crew_health_bench.medqa.domain import McqItem, build_item, item_id

_CHUNK = 1 << 16


def dataset_path(cache_dir: Path, pin: DatasetPin) -> Path:
    return cache_dir / pin.revision / pin.file


def fetch(cache_dir: Path, pin: DatasetPin, *, timeout_s: float) -> Path:
    """Download once with a size bound, verify SHA-256, then move into place atomically."""
    target = dataset_path(cache_dir, pin)
    if target.is_file():
        verify(target, pin)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    checksum, written = sha256(), 0
    try:
        with httpx.stream("GET", pin.url, timeout=timeout_s, follow_redirects=True) as response:
            if response.status_code != 200:
                raise BenchError("medqa_fetch_failed", detail=str(response.status_code))
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > pin.size_bytes:
                        raise BenchError("medqa_dataset_size")
                    checksum.update(chunk)
                    stream.write(chunk)
    except httpx.HTTPError as exc:
        raise BenchError("medqa_fetch_failed", detail=type(exc).__name__) from exc
    if written != pin.size_bytes or checksum.hexdigest() != pin.sha256:
        partial.unlink(missing_ok=True)
        raise BenchError("medqa_dataset_sha256")
    os.replace(partial, target)
    return target


def verify(path: Path, pin: DatasetPin) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != pin.size_bytes:
        raise BenchError("medqa_dataset_size")
    checksum = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != pin.sha256:
        raise BenchError("medqa_dataset_sha256")


def load(path: Path, pin: DatasetPin, *, max_line_bytes: int) -> dict[str, McqItem]:
    verify(path, pin)
    items: dict[str, McqItem] = {}
    with path.open("rb") as stream:
        for number in range(1, pin.examples + 1):
            raw = stream.readline(max_line_bytes + 1)
            if not raw or len(raw) > max_line_bytes:
                raise BenchError("medqa_line_bound")
            identity = item_id(number, raw)
            try:
                document: object = json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                raise BenchError("medqa_item_invalid", detail=identity) from exc
            items[identity] = build_item(identity, document, pin.letters)
        if stream.read(1):
            raise BenchError("medqa_dataset_examples")
    return items
