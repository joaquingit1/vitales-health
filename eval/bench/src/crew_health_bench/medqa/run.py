"""Preregistration, generation over the frozen sample, and the report.

Model validation of a general medical knowledge proxy; never clinical validation.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.medqa.config import ProxyConfig
from crew_health_bench.medqa.domain import McqItem, Sample, outcome, preregister, render_prompt, score
from crew_health_bench.medqa.model import ChatClient, ModelIdentity


def make_sample(config: ProxyConfig, items: dict[str, McqItem], n: int) -> Sample:
    return preregister(
        tuple(items), seed=config.sample_seed, n=n, warmup_n=config.warmup_n, max_n=config.max_n
    )


def write_preregistration(path: Path, config: ProxyConfig, sample: Sample) -> None:
    if path.exists():
        raise BenchError("medqa_preregistration_exists", exit_code=5)
    document = {
        "label": config.label,
        "dataset_sha256": config.dataset.sha256,
        "config_sha256": config.sha256,
        "seed": sample.seed,
        "n": sample.n,
        "sample_sha256": sample.sha256,
        "warmup_ids": list(sample.warmup_ids),
        "item_ids": list(sample.item_ids),
        "scoring": "exact match of the parsed option letter; timeout, unparseable and missing count as wrong",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def read_preregistration(path: Path, config: ProxyConfig, items: dict[str, McqItem]) -> Sample:
    """The stored record must equal the sample recomputed from the config seed; hand edits are refused."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        n = int(document["n"])
        stored = (document["dataset_sha256"], document["config_sha256"], document["item_ids"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BenchError("medqa_preregistration_missing", exit_code=5) from exc
    sample = make_sample(config, items, n)
    expected = (config.dataset.sha256, config.sha256, list(sample.item_ids))
    if stored != expected or document.get("sample_sha256") != sample.sha256:
        raise BenchError("medqa_preregistration_mismatch", exit_code=5)
    return sample


def probe(
    client: ChatClient, config: ProxyConfig, items: dict[str, McqItem], sample: Sample
) -> list[dict[str, object]]:
    """Warm-up items only (outside every sample): sizes n and checks the parse rule before
    preregistration; never scored."""
    rows: list[dict[str, object]] = []
    for identity in sample.warmup_ids:
        reply = client.ask(render_prompt(items[identity], config.generation.answer_instruction))
        rows.append({"item_id": identity, "response": reply.content, "seconds": round(reply.seconds, 2)})
    return rows


def generate(
    client: ChatClient, config: ProxyConfig, items: dict[str, McqItem], sample: Sample, results: Path
) -> tuple[str, ...]:
    if results.exists():
        raise BenchError("medqa_results_exist", exit_code=5)
    results.parent.mkdir(parents=True, exist_ok=True)
    outcomes: list[str] = []
    letters = config.dataset.letters
    with results.open("w", encoding="utf-8") as stream:
        for index, identity in enumerate(sample.item_ids, start=1):
            item = items[identity]
            reply = client.ask(render_prompt(item, config.generation.answer_instruction))
            result = outcome(reply.content, timed_out=reply.timed_out, answer=item.answer, letters=letters)
            outcomes.append(result)
            row = {"index": index, "item_id": identity, "answer": item.answer, "response": reply.content,
                   "outcome": result, "seconds": round(reply.seconds, 3)}  # fmt: skip
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
    return tuple(outcomes)


def report(
    config: ProxyConfig,
    sample: Sample,
    outcomes: tuple[str, ...],
    *,
    identity: ModelIdentity | None,
    stand_in: str | None,
    results: Path,
    wall_seconds: float,
) -> dict[str, object]:
    result = score(outcomes, n=sample.n)
    data = config.dataset
    return {
        "label": config.label,
        "integrity": "pinned_model" if identity is not None and stand_in is None else "stand_in_not_a_score",
        "benchmark": "MedQA USMLE 4-option test split, exact match",
        "n": result.n,
        "correct": result.correct,
        "wrong": result.wrong,
        "unparseable": result.unparseable,
        "timeout_or_missing": result.timeout,
        "accuracy": round(result.accuracy, 4),
        "wilson95": [round(result.interval[0], 4), round(result.interval[1], 4)],
        "model": asdict(identity) if identity is not None else {"stand_in": stand_in},
        "dataset": {
            "repo": data.repo,
            "revision": data.revision,
            "file": data.file,
            "sha256": data.sha256,
            "license": data.license,
            "examples": data.examples,
        },  # fmt: skip
        "sample": {"seed": sample.seed, "n": sample.n, "sample_sha256": sample.sha256},
        "generation": asdict(config.generation),
        "runtime": asdict(config.runtime),
        "config_sha256": config.sha256,
        "results_sha256": sha256(results.read_bytes()).hexdigest(),
        "wall_seconds": round(wall_seconds, 1),
    }


def now() -> float:
    return time.monotonic()
