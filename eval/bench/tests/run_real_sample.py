"""Harness self test on the real HealthBench files: every `chc-bench healthbench` command, no model, no spend.

Usage, from eval/bench: `uv run --frozen python -m tests.run_real_sample [--budget SECONDS]`.

It reads the official main, Hard and Consensus files from `eval/vault/healthbench/` (ignored by
git; fetch them with `chc-bench healthbench fetch`), checks them against the pinned hashes and drives
preregistration, planning, generation, the credential refusal, grading and reporting against loopback
stand-ins for llama-server and the grader. The only change to the plan is a small budget, so the
preregistered sample stays a few examples long. Everything is written to a temporary directory and
deleted. It prints mechanics (counts, states, statuses, leak checks), never a score: the grader
stand-in answers from a hash, so its numbers say nothing about any model.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from crew_health_bench.api.cli import run
from tests.healthbench_stack import BENCH, REPOSITORY, Workspace, serve


def _call(arguments: list[str], expected: int) -> dict[str, object]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run(arguments)
    if code != expected:
        raise SystemExit("exit " + str(code) + " for " + arguments[3] + ": " + err.getvalue()[-400:])
    lines = [line for line in (out.getvalue() + err.getvalue()).splitlines() if line.startswith("{")]
    value = json.loads(lines[-1]) if lines else {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=5)
    budget = parser.parse_args().budget
    vault = REPOSITORY / "eval/vault/healthbench"
    summary: dict[str, object] = {"kind": "harness self test, not a result", "budget_s": budget}
    with tempfile.TemporaryDirectory() as directory:
        space = Workspace(Path(directory).resolve())
        plan = json.loads((space.config / "healthbench-plan.json").read_bytes())
        space.edit_plan(budget={"value": budget, "unit": "s"})
        os.environ["CHC_EVAL_IDENTITY"] = str(space.identity_path)
        os.environ.pop("OPENAI_API_KEY", None)
        config = ["--config-dir", str(space.config), "healthbench"]
        common = [
            "--root",
            str(space.runs),
            "--corpus",
            str(space.corpus),
            "--model-release",
            str(space.release),
        ]
        for row in plan["datasets"]:
            source = vault / str(row["url"]).rsplit("/", 1)[1]
            _call(
                [
                    *config,
                    "fetch",
                    "--root",
                    str(space.corpus),
                    "--variant",
                    str(row["variant"]),
                    str(source),
                ],
                0,
            )
        summary["datasets_verified"] = {str(row["variant"]): row["sha256"] for row in plan["datasets"]}
        with (
            serve("model", weights_sha256=space.weights_sha256, context_tokens=space.context_tokens) as model,
            serve("grader") as grader,
        ):
            space.pin_grader(grader.url)
            _call([*config, "preregister", *common, "--base-url", model.url], 0)
            registration = json.loads((space.runs / "preregistration.json").read_bytes())
            summary["preregistered"] = {
                name: {"N": row["N"], "n": row["n"], "warmup": len(row["warmup_ids"])}
                for name, row in registration["variants"].items()
            }
            main_row = registration["variants"]["main"]
            plan_input = space.plan_input(variant="main", case_ids=list(main_row["case_ids"]))
            run_id = str(_call([*config, "plan", *common, "--manifest", str(plan_input)], 0)["run_id"])
            summary["generate"] = _call(
                [*config, "generate", *common, "--run-id", run_id, "--base-url", model.url], 0
            )["state"]
            refusal = _call([*config, "grade", *common, "--run-id", run_id, "--grader-url", grader.url], 6)
            summary["grade_without_credential"] = refusal.get("type")
            summary["grader_calls_without_credential"] = len(grader.requests)
            os.environ["OPENAI_API_KEY"] = "self-test-grader-key"
            summary["grade"] = _call(
                [*config, "grade", *common, "--run-id", run_id, "--grader-url", grader.url], 0
            )["state"]
            summary["grader_calls"] = len(grader.requests)
            summary["report"] = _call(
                [
                    *config,
                    "report",
                    *common,
                    "--run-id",
                    run_id,
                    "--grader-url",
                    grader.url,
                    "--salt",
                    str(space.salt),
                    "--published",
                    str(space.published),
                ],
                0,
            )["state"]
        published = space.published / run_id
        rows = [
            json.loads(line) for line in (published / "case_results.jsonl").read_text("utf-8").splitlines()
        ]
        summary["case_rows"] = len(rows)
        summary["statuses"] = dict(Counter(str(row.get("status")) for row in rows))
        blob = "".join(path.read_text(encoding="utf-8") for path in sorted(published.iterdir()))
        corpus = (space.corpus / "main.jsonl").read_text(encoding="utf-8").splitlines()
        sample = {json.loads(line)["prompt_id"]: json.loads(line) for line in corpus}
        leaks = [
            key
            for key in main_row["case_ids"]
            if key in blob
            or sample[key]["prompt"][-1]["content"][:80] in blob
            or any(item["criterion"][:80] in blob for item in sample[key]["rubrics"])
        ]
        summary["published_files"] = sorted(path.name for path in published.iterdir())
        summary["published_leaks"] = leaks
        summary["canary_in_published"] = str(sample[main_row["case_ids"][0]]["canary"]) in blob
        summary["grader_key_in_published"] = "self-test-grader-key" in blob
        aggregate = json.loads((published / "aggregate.json").read_bytes())
        summary["labels"] = {
            key: aggregate.get(key)
            for key in (
                "claim_class",
                "track",
                "variant",
                "completeness",
                "integrity",
                "reviewer_qualification",
            )
        }
    print(json.dumps(summary, indent=2, sort_keys=True))
    clean = (
        not leaks
        and not summary["canary_in_published"]
        and not summary["grader_key_in_published"]
        and summary["grader_calls_without_credential"] == 0
    )
    return 0 if clean else 1


if __name__ == "__main__":
    sys.path.insert(0, str(BENCH))
    raise SystemExit(main())
