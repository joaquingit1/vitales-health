"""Pure release admission. A candidate is not an evaluated runtime."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping


class ModelRefused(ValueError):
    """A safe code only; never include private input or server output."""


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ModelRefused("manifest_invalid")
    return value


def positive(value: object, *, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ModelRefused("manifest_invalid")
    return value


def sha(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ModelRefused("hash_invalid")
    return value


def validate_manifest(document: Mapping[str, object], *, profile: str) -> None:
    required = {
        "release_id",
        "created_at",
        "status",
        "mode",
        "model",
        "tokenizer_sha256",
        "chat_template_sha256",
        "thinking_mode",
        "runtime",
        "decoding",
        "tool_call_mode",
        "prompt_version",
        "tool_catalog_version",
        "context_settings",
        "retrieval_settings",
        "verifier",
        "controller",
        "embedding",
        "evaluated_with",
        "evaluation",
        "previous_release_id",
        "basis",
    }
    if not required <= document.keys() or document.keys() - required - {"weights", "stub_script_sha256"}:
        raise ModelRefused("manifest_invalid")
    if not isinstance(document["release_id"], str) or not re.fullmatch(
        "[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}", str(document["release_id"])
    ):
        raise ModelRefused("manifest_invalid")
    if document["status"] not in ("candidate", "promoted", "retired", "rejected") or document["mode"] not in (
        "live",
        "stub",
    ):
        raise ModelRefused("manifest_invalid")
    if document["mode"] == "stub" and profile not in ("demo", "eval"):
        raise ModelRefused("stub_in_flight")
    if document["thinking_mode"] != "on" or document["tool_call_mode"] not in ("native", "envelope"):
        raise ModelRefused("manifest_invalid")
    basis = object_value(document["basis"])
    runtime = object_value(document["runtime"])
    if runtime.get("parallel_slots") != 1:
        raise ModelRefused("manifest_invalid")
    positive(runtime.get("context_tokens"), maximum=262144)
    sha(document["tokenizer_sha256"])
    sha(document["chat_template_sha256"])
    _numbers(document, "", basis)
    if document["mode"] == "live":
        weights = object_value(document.get("weights"))
        digest = sha(weights.get("weights_sha256"))
        if weights.get("file_name") != digest + ".gguf":
            raise ModelRefused("lineage_broken")
        positive(weights.get("size_bytes"))
        lineage = object_value(weights.get("lineage"))
        for key in (
            "source_files_sha256",
            "bf16_gguf_sha256",
            "equivalence_report_sha256",
            "reproducibility_report_sha256",
        ):
            sha(lineage.get(key))
        if not isinstance(lineage.get("llama_cpp_commit"), str) or not re.fullmatch(
            "[a-f0-9]{40}", str(lineage["llama_cpp_commit"])
        ):
            raise ModelRefused("lineage_broken")
    elif "weights" in document:
        raise ModelRefused("manifest_invalid")


def _numbers(value: object, path: str, basis: dict[str, object]) -> None:
    if path == "basis":
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ModelRefused("manifest_invalid")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not isinstance(basis.get(path), str) or not basis[path]:
            raise ModelRefused("manifest_invalid")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _numbers(item, f"{path}.{key}" if path else str(key), basis)
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            _numbers(item, f"{path}.{i}", basis)


def strict_json(raw: str | bytes, *, max_bytes: int) -> object:
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > max_bytes:
        raise ModelRefused("payload_too_large")
    try:
        value: object = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_finite_number
        )
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ModelRefused("malformed_json") from error


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite")


def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite")
    return number
