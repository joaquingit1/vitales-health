"""The real `chc-bench healthbench` commands end to end: fetch, preregister, plan, generate, grade, report.

Synthetic six-example datasets (design section 12: never the real dataset in tests), the pinned
simple-evals wheel, real age identities, real subprocess isolation for generation and loopback
stand-ins for llama-server and the grader.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from crew_health.kernel.units import Quantity

from crew_health_bench.adapters import providers
from crew_health_bench.api.cli import run
from crew_health_bench.domain.model import BenchmarkAnswer, ChatMessage
from tests.healthbench_stack import Workspace, serve

pytestmark = [pytest.mark.e18]


def _fetch_all(space: Workspace) -> None:
    for variant, source in space.synthetic_datasets(6).items():
        assert (
            run(
                [
                    "--config-dir",
                    str(space.config),
                    "healthbench",
                    "fetch",
                    "--root",
                    str(space.corpus),
                    "--variant",
                    variant,
                    str(source),
                ]
            )
            == 0
        )


def _common(space: Workspace) -> list[str]:
    return ["--root", str(space.runs), "--corpus", str(space.corpus), "--model-release", str(space.release)]


def _last_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def _preregistered(space: Workspace, monkeypatch: pytest.MonkeyPatch, model_url: str) -> dict[str, object]:
    monkeypatch.setenv("CHC_EVAL_IDENTITY", str(space.identity_path))
    _fetch_all(space)
    assert (
        run(
            [
                "--config-dir",
                str(space.config),
                "healthbench",
                "preregister",
                *_common(space),
                "--base-url",
                model_url,
            ]
        )
        == 0
    )
    registration = json.loads((space.runs / "preregistration.json").read_bytes())
    assert isinstance(registration, dict)
    return registration


@pytest.mark.trace("E18-02", "R22", "R30", "T-E18-10", "T-E18-13", "T-E18-15", "T-E18-28", "INV-E18-05")
def test_model_only_track_runs_end_to_end_through_the_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    space = Workspace(tmp_path)
    space.edit_plan(budget={"value": 3600, "unit": "s"})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with (
        serve("model", weights_sha256=space.weights_sha256, context_tokens=space.context_tokens) as model,
        serve("grader") as grader,
    ):
        space.pin_grader(grader.url)
        registration = _preregistered(space, monkeypatch, model.url)
        main = registration["variants"]["main"]  # type: ignore[index]
        assert main["N"] == 3 and main["n"] == 3 and len(main["warmup_ids"]) == 3
        assert not set(main["case_ids"]) & set(main["warmup_ids"])
        plan_input = space.plan_input(variant="main", case_ids=list(main["case_ids"]))
        config = ["--config-dir", str(space.config), "healthbench"]
        assert run([*config, "plan", *_common(space), "--manifest", str(plan_input)]) == 0
        run_id = str(_last_json(capsys)["run_id"])
        assert run([*config, "generate", *_common(space), "--run-id", run_id, "--base-url", model.url]) == 0
        assert _last_json(capsys)["state"] == "generated"
        model_calls = [path for method, path, _ in model.requests if method == "POST"]
        assert len(model_calls) == 3 + 3 * 3, "three main cases plus three warm-up cases per variant"
        assert run([*config, "grade", *_common(space), "--run-id", run_id, "--grader-url", grader.url]) == 6
        assert grader.requests == [], "no grader call without the owner's credential"
        monkeypatch.setenv("OPENAI_API_KEY", "self-test-grader-key")
        assert run([*config, "grade", *_common(space), "--run-id", run_id, "--grader-url", model.url]) == 6
        assert grader.requests == [] and "grader_unpinned" in capsys.readouterr().err, "B2: never the model"
        assert run([*config, "grade", *_common(space), "--run-id", run_id, "--grader-url", grader.url]) == 0
        assert _last_json(capsys)["state"] == "graded"
        assert len(grader.requests) == 3 * 3, "three rubric lines for each of three cases"
        assert {auth["authorization"] for _, _, auth in grader.requests} == {"Bearer self-test-grader-key"}
        assert (
            run(
                [
                    *config,
                    "report",
                    *_common(space),
                    "--run-id",
                    run_id,
                    "--grader-url",
                    grader.url,
                    "--salt",
                    str(space.salt),
                    "--published",
                    str(space.published),
                ]
            )
            == 0
        )
        assert _last_json(capsys)["state"] == "reported"
    published = space.published / run_id
    assert sorted(path.name for path in published.iterdir()) == [
        "REPORT.md",
        "aggregate.json",
        "case_results.jsonl",
        "privacy_scan.json",
        "run_manifest.json",
    ]
    blob = "".join(path.read_text(encoding="utf-8") for path in published.iterdir())
    assert "self-test-grader-key" not in blob
    assert "synthetic-healthbench-canary" not in blob and "Synthetic protocol prompt" not in blob
    assert "Stand-in completion" not in blob and "synthetic-0" not in blob
    aggregate = json.loads((published / "aggregate.json").read_bytes())
    labels = json.dumps(aggregate)
    for label in ("public_benchmark", "model_only", "engineering"):
        assert label in labels
    assert aggregate["official"]["graded"] == 3 and aggregate["official"]["not_graded"] == 0
    assert aggregate["integrity"] == "modified", "a loopback grader pin is a self-test, never official"
    rows = [json.loads(line) for line in (published / "case_results.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == 3


@pytest.mark.trace("E18-02", "INV-E18-07", "T-E18-14")
def test_grade_without_credential_is_a_typed_refusal_that_records_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    space = Workspace(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code = run(
        [
            "--config-dir",
            str(space.config),
            "healthbench",
            "grade",
            *_common(space),
            "--run-id",
            "benchrun_any",
            "--grader-url",
            "https://api.openai.com",
        ]
    )
    assert code == 6
    problem = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert problem["type"] == "urn:crew-health:bench:grader_credential_missing"
    assert "OPENAI_API_KEY" in problem["detail"] and "Q-E18-1" in problem["detail"]
    assert not space.runs.exists()


@pytest.mark.trace("E18-02", "INV-E18-03")
def test_generation_worker_refuses_a_grader_key_or_vault_identity(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    for variable in ("OPENAI_API_KEY", "CHC_EVAL_IDENTITY"):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "crew_health_bench.api.model_worker",
                "--config-dir",
                str(space.config),
                "--model-release",
                str(space.release),
                "--schema",
                str(space.repository / "model/schemas/model-release.schema.json"),
                "--base-url",
                "http://127.0.0.1:9",
            ],
            input=b"{}",
            capture_output=True,
            env={variable: "present"},
            timeout=120,
            check=False,
        )
        assert process.returncode == 5, process.stderr
        assert json.loads(process.stderr)["reason"] == "holdout_key_required"


@pytest.mark.trace("E18-02", "R22", "T-E18-22")
@pytest.mark.parametrize(
    "served,status,reason",
    [
        (b"pinned bytes\n", 200, None),
        (b"other bytes!\n", 200, "dataset_hash_mismatch"),
        (b"pinned bytes\nand more", 200, "dataset_hash_mismatch"),
        (b"", 503, "network_unavailable"),
    ],
)
def test_fetch_streams_the_pinned_file_and_refuses_anything_else(
    tmp_path: Path,
    served: bytes,
    status: int,
    reason: str | None,
) -> None:
    from hashlib import sha256

    import httpx
    from crew_health.kernel.units import Quantity, Unit

    from crew_health_bench.adapters.healthbench_corpus import download_dataset
    from crew_health_bench.domain.errors import BenchError

    pinned = b"pinned bytes\n"
    row: dict[str, object] = {
        "variant": "main",
        "sha256": sha256(pinned).hexdigest(),
        "bytes": len(pinned),
        "url": "https://openaipublic.blob.core.windows.net/simple-evals/healthbench/x.jsonl",
        "examples": 1,
    }
    target = tmp_path / "x.jsonl"
    seconds = Quantity(Decimal(5), Unit("s"))
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=served))
    ) as client:
        if reason is None:
            assert (
                download_dataset(
                    client, row, target, timeout=seconds, connect_timeout=seconds, chunk_bytes=4
                ).read_bytes()
                == pinned
            )
        else:
            with pytest.raises(BenchError, match=reason):
                download_dataset(client, row, target, timeout=seconds, connect_timeout=seconds, chunk_bytes=4)
            assert not target.exists()
    assert not (tmp_path / "x.jsonl.part").exists()


@pytest.mark.trace("R23", "V33", "T-E18-03", "T-E18-04", "INV-E18-02")
def test_healthbench_fingerprints_catch_rubric_text_and_the_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    space = Workspace(tmp_path)
    policy_path = space.config / "partition-policy.json"
    policy = json.loads(policy_path.read_bytes())
    policy["fingerprint_sample_modulus"] = 1
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    _fetch_all(space)
    config = ["--config-dir", str(space.config)]
    assert (
        run(
            [
                *config,
                "healthbench",
                "fingerprints",
                "--root",
                str(space.corpus),
                "--corpus",
                str(space.corpus),
            ]
        )
        == 0
    )
    written = json.loads((space.config.parent / "fingerprints/healthbench.fp.json").read_bytes())
    assert len(written["cases"]) == 6, "examples shared by the three variants are fingerprinted once"
    assert {len(row["parts"]) for row in written["cases"]} == {4}, "the prompt and three rubric lines apart"
    assert "Synthetic" not in json.dumps(written) and "synthetic-0" not in json.dumps(written)
    fingerprints = str(space.config.parent / "fingerprints/healthbench.fp.json")
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "prompt.txt").write_text(
        "You are a careful assistant for a crew medical officer.", encoding="utf-8"
    )
    assert (
        run([*config, "leak", "scan", "--fingerprints", fingerprints, "--target", "prompts", str(clean)]) == 0
    )
    capsys.readouterr()
    leaked = tmp_path / "leaked"
    leaked.mkdir()
    (leaked / "few-shot.txt").write_text(
        "Few-shot: synthetic second criterion 4 tells the reader to rest, drink water and recheck.",
        encoding="utf-8",
    )
    (leaked / "passage.txt").write_text("Passage text synthetic-healthbench-canary end.", encoding="utf-8")
    assert (
        run([*config, "leak", "scan", "--fingerprints", fingerprints, "--target", "examples", str(leaked)])
        == 3
    )
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert {(Path(row["path"]).name, row["kind"]) for row in findings} >= {
        ("few-shot.txt", "containment"),
        ("passage.txt", "canary"),
    }


class StandInAnswerPort:
    """A structural stand-in for E13's `BenchmarkAnswerPort` (design 4.5) over the loopback model server.

    E13 has not provided the port; this one only forwards the messages, so its answers say nothing about a
    model.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.calls: list[tuple[str, str]] = []

    def answer(
        self,
        messages: Sequence[ChatMessage],
        *,
        evidence_release_id: str,
        tool_profile: str,
        timeout: Quantity,
    ) -> BenchmarkAnswer:
        self.calls.append((evidence_release_id, tool_profile))
        response = httpx.post(
            self.base_url + "/v1/chat/completions",
            json={"messages": [{"role": item.role, "content": item.content} for item in messages]},
            timeout=float(timeout.value),
            trust_env=False,
        )
        choice = response.json()["choices"][0]
        return BenchmarkAnswer(choice["message"]["content"], (), (), (), choice["finish_reason"])


@pytest.mark.trace("E18-02", "INV-E18-03", "INV-E18-05", "INV-E18-08")
def test_system_assisted_track_refuses_without_the_answer_port_and_runs_end_to_end_with_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    space = Workspace(tmp_path)
    space.edit_plan(budget={"value": 3600, "unit": "s"})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with (
        serve("model", weights_sha256=space.weights_sha256, context_tokens=space.context_tokens) as model,
        serve("grader") as grader,
    ):
        space.pin_grader(grader.url)
        registration = _preregistered(space, monkeypatch, model.url)
        before = len(model.requests)
        cases = list(registration["variants"]["main"]["case_ids"])  # type: ignore[index]
        plan_input = space.plan_input(variant="main", case_ids=cases)
        manifest = json.loads(plan_input.read_bytes())
        manifest["track"] = "system_assisted"
        plan_input.write_text(json.dumps(manifest), encoding="utf-8")
        config = ["--config-dir", str(space.config), "healthbench"]
        assert run([*config, "plan", *_common(space), "--manifest", str(plan_input)]) == 0
        run_id = str(_last_json(capsys)["run_id"])
        generate = [*config, "generate", *_common(space), "--run-id", run_id, "--base-url", model.url]
        assert run(generate) == 5, "generation never runs with the vault identity present"
        assert json.loads(capsys.readouterr().err.splitlines()[-1])["title"] == "holdout_key_required"
        monkeypatch.delenv("CHC_EVAL_IDENTITY")
        assert run(generate) == 6
        problem = json.loads(capsys.readouterr().err.splitlines()[-1])
        assert problem["type"] == "urn:crew-health:bench:no_evaluation_entry_point"
        assert "BenchmarkAnswerPort" in problem["detail"]
        assert len(model.requests) == before, (
            "the model is never called directly on the system-assisted track"
        )
        assert not (space.runs / run_id / "generations.jsonl").exists()
        port = StandInAnswerPort(model.url)
        monkeypatch.setattr(providers, "benchmark_answer_port", lambda release: port)
        assert run(generate) == 0
        assert _last_json(capsys)["state"] == "generated"
        assert len(port.calls) == len(cases)
        assert {profile for _, profile in port.calls} == {"benchmark_readonly"}
        monkeypatch.setenv("CHC_EVAL_IDENTITY", str(space.identity_path))
        monkeypatch.setenv("OPENAI_API_KEY", "self-test-grader-key")
        grade = [*config, "grade", *_common(space), "--run-id", run_id, "--grader-url", grader.url]
        assert run(grade) == 0
        published = ["--salt", str(space.salt), "--published", str(space.published)]
        assert (
            run(
                [
                    *config,
                    "report",
                    *_common(space),
                    "--run-id",
                    run_id,
                    "--grader-url",
                    grader.url,
                    *published,
                ]
            )
            == 0
        )
        assert _last_json(capsys)["state"] == "reported"
    aggregate = json.loads((space.published / run_id / "aggregate.json").read_bytes())
    assert "system_assisted" in json.dumps(aggregate)
    assert aggregate["official"]["graded"] == len(cases)


@pytest.mark.trace("E18-02", "R22", "INV-E18-05", "T-E18-13")
def test_a_hand_written_preregistration_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B3: the sample is the stored freeze record and the seeded prefix, never whatever the file says."""
    space = Workspace(tmp_path)
    space.edit_plan(budget={"value": 3600, "unit": "s"})
    with serve("model", weights_sha256=space.weights_sha256, context_tokens=space.context_tokens) as model:
        registration = _preregistered(space, monkeypatch, model.url)
    main = registration["variants"]["main"]  # type: ignore[index]
    config = ["--config-dir", str(space.config), "healthbench"]
    stored = space.runs / "preregistration.json"
    original = stored.read_bytes()
    picked = json.loads(original)
    picked["variants"]["main"].update(case_ids=list(main["warmup_ids"]), warmup_ids=[])
    shuffled = json.loads(original)
    shuffled["variants"]["main"]["case_ids"] = list(reversed(main["case_ids"]))
    for record in (picked, shuffled, {**json.loads(original), "frozen_by": "anyone"}):
        stored.write_text(json.dumps(record), encoding="utf-8")
        chosen = space.plan_input(variant="main", case_ids=list(record["variants"]["main"]["case_ids"]))
        assert run([*config, "plan", *_common(space), "--manifest", str(chosen)]) == 6
        problem = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert problem["title"] == "preregistration_mismatch"
    stored.write_bytes(original)
    honest = space.plan_input(variant="main", case_ids=list(main["case_ids"]))
    assert run([*config, "plan", *_common(space), "--manifest", str(honest)]) == 0


@pytest.mark.trace("E18-02", "R22", "T-E18-19")
def test_a_model_server_outage_aborts_the_run_instead_of_recording_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M1: connection refused before any completion is `aborted(model_unavailable)`, exit 6, no records."""
    space = Workspace(tmp_path)
    space.edit_plan(budget={"value": 3600, "unit": "s"})
    with serve("model", weights_sha256=space.weights_sha256, context_tokens=space.context_tokens) as model:
        registration = _preregistered(space, monkeypatch, model.url)
        url = model.url
    monkeypatch.delenv("CHC_EVAL_IDENTITY")
    main = registration["variants"]["main"]  # type: ignore[index]
    config = ["--config-dir", str(space.config), "healthbench"]
    plan_input = space.plan_input(variant="main", case_ids=list(main["case_ids"]))
    assert run([*config, "plan", *_common(space), "--manifest", str(plan_input)]) == 0
    run_id = str(_last_json(capsys)["run_id"])
    assert run([*config, "generate", *_common(space), "--run-id", run_id, "--base-url", url]) == 6
    problem = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert problem["title"] == "model_unavailable"
    lines = (space.runs / run_id / "state.jsonl").read_text("utf-8").splitlines()
    states = [json.loads(line)["record"]["state"] for line in lines]
    assert states[-1] == "aborted"
    assert not (space.runs / run_id / "generations.jsonl").exists()
