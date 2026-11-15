"""Typed view of config/medqa-proxy.json; every value the run uses comes from this document."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from crew_health_bench.domain.errors import BenchError

_MAX_CONFIG_BYTES = 1 << 16


@dataclass(frozen=True, slots=True)
class DatasetPin:
    repo: str
    revision: str
    file: str
    url: str
    sha256: str
    size_bytes: int
    examples: int
    license: str
    letters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelPin:
    file_name: str
    weights_sha256: str
    source: str
    chat_template_sha256: str
    binary_sha256: str
    runtime_build: str
    runtime_commit: str


@dataclass(frozen=True, slots=True)
class Runtime:
    context_tokens: int
    threads: int
    gpu_layers: int
    kv_cache_type: str
    load_timeout_s: float
    health_poll_s: float


@dataclass(frozen=True, slots=True)
class Generation:
    temperature: float
    top_k: int
    seed: int
    max_tokens: int
    enable_thinking: bool
    item_timeout_s: float
    system: str
    answer_instruction: str


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    label: str
    dataset: DatasetPin
    model: ModelPin
    runtime: Runtime
    generation: Generation
    sample_seed: int
    max_n: int
    warmup_n: int
    max_line_bytes: int
    max_response_chars: int
    connect_timeout_s: float
    sha256: str


def _section(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise BenchError("medqa_config_invalid", detail=key)
    return value


def _positive(value: object, key: str, *, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= upper:
        raise BenchError("medqa_config_invalid", detail=key)
    return float(value)


def _int(value: object, key: str, *, lower: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise BenchError("medqa_config_invalid", detail=key)
    return value


def load_config(path: Path) -> ProxyConfig:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BenchError("medqa_config_unavailable") from exc
    if len(raw) > _MAX_CONFIG_BYTES:
        raise BenchError("medqa_config_invalid", detail="size")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise BenchError("medqa_config_invalid")
    try:
        return _build(document, sha256(raw).hexdigest())
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchError("medqa_config_invalid", detail=str(exc)) from exc


def _build(document: dict[str, object], digest: str) -> ProxyConfig:
    data, model = _section(document, "dataset"), _section(document, "model")
    run, gen = _section(document, "runtime"), _section(document, "generation")
    sample, bounds = _section(document, "sample"), _section(document, "bounds")
    letters = data["letters"]
    if not isinstance(letters, list) or not letters:
        raise BenchError("medqa_config_invalid", detail="letters")
    if gen["temperature"] != 0 or gen["enable_thinking"] is not False:
        raise BenchError("medqa_config_invalid", detail="greedy decoding without thinking is required")
    label = str(document["label"])
    if "not clinical validation" not in label:
        raise BenchError("medqa_config_invalid", detail="label")
    return ProxyConfig(
        label=label,
        dataset=DatasetPin(
            str(data["repo"]),
            str(data["revision"]),
            str(data["file"]),
            str(data["url"]),
            str(data["sha256"]),
            int(_positive(data["size_bytes"], "size_bytes", upper=1 << 30)),
            int(_positive(data["examples"], "examples", upper=1 << 20)),
            str(data["license"]),
            tuple(str(item) for item in letters),
        ),
        model=ModelPin(
            str(model["file_name"]),
            str(model["weights_sha256"]),
            str(model["source"]),
            str(model["chat_template_sha256"]),
            str(model["binary_sha256"]),
            str(model["runtime_build"]),
            str(model["runtime_commit"]),
        ),
        runtime=Runtime(
            int(_positive(run["context_tokens"], "context_tokens", upper=262144)),
            int(_positive(run["threads"], "threads", upper=6)),
            _int(run["gpu_layers"], "gpu_layers", lower=0, upper=999),
            str(run["kv_cache_type"]),
            _positive(run["load_timeout_s"], "load_timeout_s", upper=600),
            _positive(run["health_poll_s"], "health_poll_s", upper=10),
        ),
        generation=Generation(
            0.0,
            _int(gen["top_k"], "top_k", lower=1, upper=1),
            _int(gen["seed"], "seed", lower=0, upper=1 << 31),
            int(_positive(gen["max_tokens"], "max_tokens", upper=256)),
            False,
            _positive(gen["item_timeout_s"], "item_timeout_s", upper=600),
            str(gen["system"]),
            str(gen["answer_instruction"]),
        ),
        sample_seed=_int(sample["seed"], "sample_seed", lower=0, upper=1 << 62),
        max_n=int(_positive(sample["max_n"], "max_n", upper=1 << 20)),
        warmup_n=_int(sample["warmup_n"], "warmup_n", lower=0, upper=100),
        max_line_bytes=int(_positive(bounds["max_line_bytes"], "max_line_bytes", upper=1 << 20)),
        max_response_chars=int(_positive(bounds["max_response_chars"], "max_response_chars", upper=1 << 16)),
        connect_timeout_s=_positive(bounds["connect_timeout_s"], "connect_timeout_s", upper=60),
        sha256=digest,
    )
