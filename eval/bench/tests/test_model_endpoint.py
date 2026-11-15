from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from crew_health.kernel.units import Quantity, Unit
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.adapters.documents import BenchSettings, read_json
from crew_health_bench.adapters.model_endpoint import LlamaEndpoint
from crew_health_bench.adapters.model_release import load_release
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.model import ChatMessage

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parents[1] / "model/schemas/model-release.schema.json"


def settings() -> BenchSettings:
    return BenchSettings(read_json(ROOT / "config/bench-settings.json", max_file_bytes=1048576))


def fixture() -> dict[str, object]:
    phases = dict.fromkeys(
        ("intake", "assessment", "disposition", "help", "insight", "general", "verifier"), 1
    )
    doc: dict[str, object] = {
        "release_id": "protocol-release",
        "created_at": "2026-11-14T00:00:00Z",
        "status": "candidate",
        "mode": "live",
        "model": {
            "model_id": "protocol-model",
            "hf_repo": "protocol",
            "revision": "protocol",
            "license": "protocol",
            "license_sha256": "a" * 64,
            "files": [],
        },
        "weights": {
            "file_name": "b" * 64 + ".gguf",
            "weights_sha256": "b" * 64,
            "size_bytes": 1,
            "quantization": "protocol",
            "lineage": {
                "source_files_sha256": [],
                "bf16_gguf_sha256": "c" * 64,
                "equivalence_report_sha256": "d" * 64,
                "reproducibility_report_sha256": "e" * 64,
                "llama_cpp_commit": "protocol",
            },
        },
        "runtime": {
            "engine": "llama.cpp",
            "build": "protocol",
            "commit": "protocol",
            "backend": "protocol",
            "context_tokens": 1,
            "kv_cache_type": "protocol",
            "parallel_slots": 1,
            "threads": 1,
            "gpu_layers": 0,
            "load_timeout_seconds": 1,
            "max_queued_turns": 1,
            "max_preemptions": 0,
        },
        "decoding": {
            "temperature": 0,
            "top_p": 1,
            "top_k": 1,
            "min_p": 0,
            "seed": 1,
            "max_tokens_by_phase": phases,
            "reasoning_format": "deepseek",
        },
        "tokenizer_sha256": "f" * 64,
        "chat_template_sha256": "a" * 64,
        "thinking_mode": "on",
        "tool_call_mode": "native",
        "prompt_version": "protocol",
        "tool_catalog_version": "protocol",
        "evaluated_with": {
            "evidence_release_id": "protocol",
            "config_release_id": "protocol",
            "turn_timeout_seconds": 1,
        },
    }
    basis: dict[str, str] = {}
    stack: list[tuple[str, object]] = [("", doc)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, dict):
            stack.extend((path + "/" + str(key), item) for key, item in value.items())
        elif isinstance(value, int | float):
            basis[path] = "team default: synthetic protocol fixture, no model claim"
    doc["basis"] = basis
    return doc


@pytest.mark.trace("R22", "R23", "invariant")
def test_release_controls_decoding_and_endpoint_identity(tmp_path: Path) -> None:
    document = fixture()
    source = tmp_path / "release.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    release = load_release(source, SCHEMA, max_file_bytes=1048576)
    sent: list[dict[str, object]] = []

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(
                200,
                json={
                    "model_path": "models/" + release.weights_sha256 + ".gguf",
                    "default_generation_settings": {"n_ctx": release.context_tokens},
                },
            )
        assert request.url.path == "/v1/chat/completions"
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "protocol"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(server), trust_env=False) as client:
        endpoint = LlamaEndpoint("http://127.0.0.1:8080", settings(), client)
        endpoint.require_identity(release, timeout=release.turn_timeout)
        result = endpoint.complete(
            (ChatMessage("user", "protocol"),), release.decoding, timeout=release.turn_timeout
        )
        assert result.text == "protocol" and result.thinking_tokens is None
        assert sent[0]["max_tokens"] == 1
        modified = deepcopy(document)
        decoding = modified["decoding"]
        assert isinstance(decoding, dict)
        phases = decoding["max_tokens_by_phase"]
        assert isinstance(phases, dict)
        phases["general"] = 7
        source.write_text(json.dumps(modified), encoding="utf-8")
        changed = load_release(source, SCHEMA, max_file_bytes=1048576)
        endpoint.complete((ChatMessage("user", "protocol"),), changed.decoding, timeout=changed.turn_timeout)
        assert sent[1]["max_tokens"] == 7
        assert sent[0]["messages"] == [{"role": "user", "content": "protocol"}]
    document["mode"] = "stub"
    source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BenchError, match="release_unreadable"):
        load_release(source, SCHEMA, max_file_bytes=1048576)
    assert load_release(source, SCHEMA, max_file_bytes=1048576, self_test=True)


@pytest.mark.trace("R22", "R23", "invariant")
@given(st.sampled_from(("https://192.0.2.1", "http://localhost", "file:///private", "http://user@127.0.0.1")))
def test_endpoint_never_contacts_nonlocal_or_credentialed_target(url: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200)), trust_env=False
    ) as client:
        with pytest.raises(BenchError):
            LlamaEndpoint(url, settings(), client)


@pytest.mark.trace("R22", "invariant")
def test_bad_identity_or_body_fails_closed_without_generation_retry(tmp_path: Path) -> None:
    source = tmp_path / "release.json"
    source.write_text(json.dumps(fixture()), encoding="utf-8")
    release = load_release(source, SCHEMA, max_file_bytes=1048576)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"model_path": "wrong.gguf", "default_generation_settings": {"n_ctx": 1}}
            )
        ),
        trust_env=False,
    ) as client:
        endpoint = LlamaEndpoint("http://127.0.0.1", settings(), client)
        with pytest.raises(BenchError, match="model_identity_mismatch"):
            endpoint.require_identity(release, timeout=release.turn_timeout)
        with pytest.raises(BenchError):
            endpoint.complete(
                (ChatMessage("user", "protocol"),), release.decoding, timeout=release.turn_timeout
            )
        with pytest.raises(BenchError):
            endpoint.identity(timeout=Quantity(Decimal(1), Unit("ms")))
    document = fixture()
    document["basis"] = {"irrelevant": "team default"}
    source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BenchError, match="release_unreadable"):
        load_release(source, SCHEMA, max_file_bytes=1048576)
