from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from crew_health.model.adapters.manifest_store import ManifestStore

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def policy() -> dict[str, object]:
    return json.loads((ROOT / "model/policy/promotion-policy.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture
def gate() -> dict[str, object]:
    return {
        "release_id": "model_candidate",
        "verdict": "pass",
        "partition": "holdout",
        "completeness": "full",
        "run_ids": ["run_test"],
        "case_set_sha256": "a" * 64,
        "paired_case_set_sha256": "a" * 64,
        "failures": [],
        "regressions": {"grounding": 0.0, "assessment": 0.0, "acquisition": 0.0},
        "latency_regression_fraction": 0.0,
    }


@pytest.fixture
def manifest() -> dict[str, Any]:
    pin = json.loads((ROOT / "model/pins/huatuogpt-3-9b.json").read_text(encoding="utf-8"))
    template = next((ROOT / "model/templates").glob("*.jinja")).stem
    phases = ["intake", "assessment", "disposition", "help", "insight", "general", "verifier"]
    document = {
        "release_id": "model_candidate",
        "created_at": "2026-11-15T00:00:00Z",
        "status": "candidate",
        "mode": "stub",
        "model": {
            "model_id": "HuatuoGPT-3-9B",
            "hf_repo": pin["repo"],
            "revision": pin["revision"],
            "license": pin["license"],
            "license_sha256": pin["license_sha256"],
            "files": [{"name": f["name"], "sha256": f["sha256"]} for f in pin["files"]],
        },
        "tokenizer_sha256": next(f["sha256"] for f in pin["files"] if f["name"] == "tokenizer.json"),
        "chat_template_sha256": template,
        "thinking_mode": "on",
        "tool_call_mode": "native",
        "prompt_version": "v1",
        "tool_catalog_version": "v1",
        "runtime": {
            "engine": "llama.cpp",
            "build": "11669",
            "commit": "620d2225cc436ab0b001e3200e590e3b286ef9ab",
            "backend": "cpu",
            "context_tokens": 32768,
            "kv_cache_type": "f16",
            "parallel_slots": 1,
            "threads": 4,
            "gpu_layers": 0,
            "load_timeout_seconds": 30,
            "max_queued_turns": 4,
            "max_preemptions": 2,
        },
        "decoding": {
            "temperature": 0.2,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "seed": 17,
            "max_tokens_by_phase": {p: 64 for p in phases},
            "reasoning_format": "deepseek",
        },
        "context_settings": {
            "budget_tokens_by_phase": {p: 32768 for p in phases},
            "section_order": ["facts", "evidence"],
            "transcript_max_messages": 16,
        },
        "retrieval_settings": {
            "k_lexical": 20,
            "k_dense": 20,
            "final_min": 0,
            "final_max": 8,
            "budget_tokens": 2048,
            "max_per_parent": 2,
            "k_rrf": 60,
            "rerank": False,
        },
        "controller": {"max_iterations": 4, "max_tool_calls": 8},
        "verifier": {"claim_support_pass": False},
        "embedding": {
            "model_id": "BAAI/bge-small-en-v1.5",
            "revision": "0" * 40,
            "sha256": "1" * 64,
            "dims": 384,
        },
        "evaluated_with": {
            "evidence_release_id": "evidence_test",
            "config_release_id": "config_test",
            "turn_timeout_seconds": 60,
        },
        "evaluation": {"probe_report_ids": [], "dev_report_ids": [], "gate_run_ids": [], "gate_sha256": None},
        "previous_release_id": None,
    }
    basis = {}

    def visit(v: object, path: str = "") -> None:
        if isinstance(v, int | float) and not isinstance(v, bool):
            basis[path] = "Team assumption for hermetic transport tests, not a measured model release"
        elif isinstance(v, dict):
            for k, item in v.items():
                visit(item, f"{path}.{k}" if path else k)
        elif isinstance(v, list):
            for n, item in enumerate(v):
                visit(item, f"{path}.{n}")

    visit(document)
    document["basis"] = basis
    return document


@pytest.fixture
def store(tmp_path: Path, manifest: dict[str, Any]) -> ManifestStore:
    root = tmp_path / "model"
    shutil.copytree(ROOT / "model", root, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    (root / "releases").mkdir(exist_ok=True)
    (root / "releases/model_candidate.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ManifestStore(root, tmp_path / "data", profile="eval")
