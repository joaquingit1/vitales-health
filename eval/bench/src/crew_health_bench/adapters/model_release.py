from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.units import Quantity, Unit
from pydantic import ValidationError

from crew_health_bench.adapters.documents import load_document
from crew_health_bench.contracts_gen.model_release import ModelRelease
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.model import DecodingSettings, ModelReleaseRef


def _basis(document: dict[str, object], basis: dict[str, str]) -> None:
    stack: list[tuple[str, object]] = [("", document)]
    while stack:
        path, value = stack.pop()
        if path == "/basis":
            continue
        if isinstance(value, dict):
            stack.extend((path + "/" + str(key), child) for key, child in value.items())
        elif isinstance(value, list):
            stack.extend((path + "/" + str(index), child) for index, child in enumerate(value))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            if path not in basis:
                raise BenchError("release_unreadable")


def load_release(
    path: Path, schema: Path, *, max_file_bytes: int, self_test: bool = False
) -> ModelReleaseRef:
    document = load_document(path, max_file_bytes=max_file_bytes, schema_path=schema)
    try:
        release = ModelRelease.model_validate(document)
    except ValidationError as error:
        raise BenchError("release_unreadable") from error
    if release.mode != "live" and not self_test:
        raise BenchError("release_unreadable")
    if release.weights.file_name != release.weights.weights_sha256 + ".gguf":
        raise BenchError("model_identity_mismatch")
    _basis(document, release.basis)
    decoding = release.decoding
    settings = DecodingSettings(
        Decimal(str(decoding.temperature)),
        Decimal(str(decoding.top_p)),
        decoding.top_k,
        Decimal(str(decoding.min_p)),
        decoding.seed,
        decoding.max_tokens_by_phase.general,
        decoding.reasoning_format,
    )
    projection = {
        key: document[key]
        for key in (
            "release_id",
            "mode",
            "model",
            "weights",
            "runtime",
            "chat_template_sha256",
            "thinking_mode",
            "tool_call_mode",
            "decoding",
            "prompt_version",
            "tool_catalog_version",
            "evaluated_with",
        )
    }
    projection["sha256"] = sha256(canonical_json(document)).hexdigest()
    return ModelReleaseRef(
        release.release_id,
        release.model.model_id,
        release.weights.weights_sha256,
        release.runtime.context_tokens,
        settings,
        Quantity(Decimal(str(release.evaluated_with.turn_timeout_seconds)), Unit("s")),
        projection,
    )
