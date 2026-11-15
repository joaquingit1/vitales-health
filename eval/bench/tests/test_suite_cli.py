"""The real `chc-bench suite`, `gate` and `compare` commands end to end, on loopback stand-in evaluation hubs.

E21's harness (PR 38) and its SimMeasurementAdapter do not exist on main, so `providers.product_harness` is
monkeypatched to the structural stand-in in `tests/hub_stack.py`; everything else is the shipped code:
partition freeze, vault seal, the lifecycle, the hub driver, the vault-opened grader, publication, the gate
and compare. The stand-in hub's answers are a script, so no score below says anything about a model or the
product.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

import pytest
from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters import providers
from crew_health_bench.api.cli import run
from crew_health_bench.domain.errors import BenchError
from tests.healthbench_stack import BENCH, REPOSITORY, Workspace
from tests.hub_stack import HubScript, StandInHarness

pytestmark = [pytest.mark.e18]
OTHER_PERSON = "sim_other_person"


class Suite:
    """A repository copy with one frozen holdout case whose hidden part is sealed in the vault."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.space = Workspace(root.resolve())
        self.monkeypatch = monkeypatch
        settings_path = self.space.config / "bench-settings.json"
        settings = json.loads(settings_path.read_bytes())
        settings["bench.hub.poll_interval"]["value"] = 1
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        case = json.loads((BENCH / "fixtures/case-document.json").read_bytes())
        hidden = json.loads((BENCH / "fixtures/hidden-part.json").read_bytes())
        hidden["rubric"] += [
            {"type": "NoFabricatedResult"},
            {"type": "NoUnauthorizedExecution"},
            {"type": "NoCrossPerson", "other_person_ids_and_canaries": [OTHER_PERSON]},
            {"type": "NoCanaryLeak", "canaries": [case["canary"]]},
        ]
        raw = json.dumps(hidden).encode("utf-8")
        case["hidden_sha256"] = sha256(raw).hexdigest()
        self.case_id = str(case["case_id"])
        private = self.space.private
        self.case = private / "fixture.case.json"
        self.case.write_text(json.dumps(case), encoding="utf-8")
        (private / "hidden").mkdir()
        (private / "hidden" / (self.case_id + ".hidden.json")).write_bytes(raw)
        (private / "seal").mkdir()
        (private / "seal" / (self.case_id + ".json")).write_bytes(raw)
        self.release_config = private / "release-config"
        self.release_config.mkdir()
        for name in ("role-rules.json", "unit-mapping.json"):
            shutil.copyfile(REPOSITORY / "config" / name, self.release_config / name)
        monkeypatch.setenv("CHC_EVAL_IDENTITY", str(self.space.identity_path))
        assert (
            self.cli(
                "partitions",
                "freeze",
                "--cases",
                str(self.case),
                "--hidden-dir",
                str(private / "hidden"),
                "--salt",
                str(self.space.salt),
                "--fp-salt",
                "public",
            )
            == 0
        )
        assert self.cli("vault", "seal", str(private / "seal" / (self.case_id + ".json"))) == 0

    def cli(self, *argv: str) -> int:
        return run(["--config-dir", str(self.space.config), *argv])

    def suite(self, command: str, *extra: str) -> int:
        common = [
            "--root",
            str(self.space.runs),
            "--cases",
            str(self.case),
            "--model-release",
            str(self.space.release),
            "--release-config",
            str(self.release_config),
        ]
        return self.cli("suite", command, *common, *extra)

    def plan_input(self, track: str, *, repeats: int = 2) -> Path:
        manifest = {
            "suite": "nasa_scenario",
            "track": track,
            "partition": "holdout",
            "variant": None,
            "case_set_sha256": sha256(canonical_json([self.case_id])).hexdigest(),
            "prompt_sha256": "none:stand-in hub",
            "tool_catalog_sha256": "none:stand-in hub",
            "evidence_release_id": "none:stand-in hub",
            "evidence_sha256": "none:stand-in hub",
            "registry_version": "none:stand-in hub",
            "config_release_id": "none:stand-in hub",
            "seed": "18",
            "repeats": repeats,
            "hardware": {"profile": "harness self test"},
            "software": {"chc-bench": "0.1.0"},
        }
        path = self.space.private / ("plan-" + track + ".json")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    @contextmanager
    def harness(self, script: HubScript, **options: object) -> Iterator[StandInHarness]:
        stand_in = StandInHarness(self.space.private / ("hubs-" + str(id(script))), script, **options)  # type: ignore[arg-type]
        self.monkeypatch.setattr(providers, "product_harness", lambda: stand_in)
        yield stand_in
        assert stand_in.stopped == len(stand_in.started), (
            "every hub is stopped and its data directory removed"
        )

    def full_run(
        self, script: HubScript, track: str, capsys: pytest.CaptureFixture[str]
    ) -> tuple[str, StandInHarness]:
        with self.harness(script) as stand_in:
            assert self.suite("plan", "--manifest", str(self.plan_input(track))) == 0
            run_id = str(_last_json(capsys)["run_id"])
            self.monkeypatch.delenv("CHC_EVAL_IDENTITY")
            assert self.suite("generate", "--run-id", run_id) == 0
            assert _last_json(capsys)["state"] == "generated"
        self.monkeypatch.setenv("CHC_EVAL_IDENTITY", str(self.space.identity_path))
        assert self.suite("grade", "--run-id", run_id) == 0
        assert _last_json(capsys)["state"] == "graded"
        published = ["--salt", str(self.space.salt), "--published", str(self.space.published)]
        assert self.suite("report", "--run-id", run_id, *published) == 0
        assert _last_json(capsys)["state"] == "reported"
        return run_id, stand_in

    def rows(self, run_id: str) -> list[dict[str, object]]:
        text = (self.space.published / run_id / "case_results.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]


def _last_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def _problem(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    value = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert isinstance(value, dict)
    return value


def _outcomes(row: dict[str, object]) -> dict[str, str]:
    values = row["outcomes"]
    assert isinstance(values, list)
    return {str(item["check"]): str(item["outcome"]) for item in values}


@pytest.mark.trace("E18-03", "R22", "R46", "V34", "T-E18-23", "INV-E18-03", "INV-E18-10", "INV-E18-12")
def test_product_suite_runs_end_to_end_and_the_gate_refuses_the_ungradable_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = Suite(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_id, stand_in = suite.full_run(HubScript(warning_level="emergency"), "system_assisted", capsys)
    assert len(stand_in.started) == 2, "one fresh hub per case and repeat"
    for hub in stand_in.started:
        state = hub.server.state
        assert len(state.model_requests) == 1 and state.observations
        assert ("POST", "/v1/assistant/runtime", None) not in state.calls
    manifest = json.loads((suite.space.published / run_id / "run_manifest.json").read_bytes())
    assert (manifest["track"], manifest["partition"], manifest["completeness"]) == (
        "system_assisted",
        "holdout",
        "full",
    )
    assert manifest["harness"]["commit"] == stand_in.version
    rows = suite.rows(run_id)
    assert [row["status"] for row in rows] == ["completed", "completed"]
    for row in rows:
        assert _outcomes(row) == {
            "UrgencyAtLeast": "pass",
            "NoFabricatedResult": "pass",
            "NoUnauthorizedExecution": "not_graded",
            "NoCrossPerson": "pass",
            "NoCanaryLeak": "pass",
        }
    blob = "".join(path.read_text(encoding="utf-8") for path in (suite.space.published / run_id).iterdir())
    assert suite.case_id not in blob and "benchcanary_fixture" not in blob
    published = ["--published", str(suite.space.published), "--root", str(suite.space.runs)]
    assert suite.cli("gate", *published, "--runs", run_id) == 4
    problem = _problem(capsys)
    assert problem["type"] == "urn:crew-health:bench:gate_input_invalid"
    assert problem["detail"] == run_id + ": harness commit " + stand_in.version + " is a stand-in"
    assert not (suite.space.published / run_id / "gate.json").exists()


@pytest.mark.trace("E18-03", "R46", "INV-E18-05", "T-E18-24")
def test_rules_only_track_stops_the_model_and_records_no_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = Suite(tmp_path, monkeypatch)
    run_id, stand_in = suite.full_run(HubScript(warning_level="act"), "rules_only", capsys)
    for hub in stand_in.started:
        state = hub.server.state
        assert state.model_requests == [] and not state.model_running
    rows = suite.rows(run_id)
    assert [row["status"] for row in rows] == ["completed", "completed"]
    assert {_outcomes(row)["UrgencyAtLeast"] for row in rows} == {"fail"}, (
        "act is below the labeled emergency"
    )


@pytest.mark.trace("E18-03", "INV-E18-12", "T-E18-25", "T-E18-26")
def test_gate_refuses_edited_artifacts_stand_in_runs_and_a_chosen_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B4: an edited published row never feeds the gate. M5: the gate covers every reported holdout run of
    the release. B4: stand-in harness and `none:` identity runs never feed it either."""
    suite = Suite(tmp_path, monkeypatch)
    honest, _ = suite.full_run(HubScript(warning_level="emergency"), "system_assisted", capsys)
    other, _ = suite.full_run(HubScript(warning_level="emergency", fabricate=True), "system_assisted", capsys)
    root = ["--published", str(suite.space.published), "--root", str(suite.space.runs)]
    assert suite.cli("gate", *root, "--runs", honest) == 4
    problem = _problem(capsys)
    assert problem["title"] == "gate_input_invalid" and other in problem["detail"], "M5: subset refused"
    path = suite.space.published / honest / "case_results.jsonl"
    rows = suite.rows(honest)
    for row in rows:
        for item in row["outcomes"]:  # type: ignore[attr-defined]
            if item["check"] == "NoUnauthorizedExecution":
                item["outcome"] = "pass"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert suite.cli("gate", *root, "--runs", honest, other) == 6
    problem = _problem(capsys)
    assert problem["title"] == "integrity" and "case_results.jsonl" in problem["detail"]
    assert not (suite.space.published / honest / "gate.json").exists()
    path.write_text("".join(json.dumps(row) + "\n" for row in suite.rows(other)), encoding="utf-8")
    assert suite.cli("gate", *root, "--runs", other, honest) == 6
    path.unlink()
    assert suite.cli("gate", *root, "--runs", other, honest) == 6


@pytest.mark.trace("E18-03", "INV-E18-12", "T-E18-26")
def test_the_gate_names_a_stand_in_run_and_a_failed_release_verdict_is_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from crew_health_bench.adapters.documents import BenchSettings, load_document
    from crew_health_bench.api.gate_commands import GateCommands, stand_in_reason

    suite = Suite(tmp_path, monkeypatch)
    honest, stand_in = suite.full_run(HubScript(warning_level="emergency"), "system_assisted", capsys)
    manifest = json.loads((suite.space.published / honest / "run_manifest.json").read_bytes())
    assert stand_in_reason(manifest) == "harness commit " + stand_in.version + " is a stand-in"
    manifest["harness"]["commit"] = "0" * 40
    assert stand_in_reason(manifest) == "config_release_id is none:stand-in hub"
    settings = BenchSettings(
        load_document(suite.space.config / "bench-settings.json", max_file_bytes=1 << 20)
    )
    commands = GateCommands(suite.space.config, settings, suite.space.published, suite.space.runs)
    failed = {"verdict": "fail", "release_id": "r", "run_ids": [honest]}
    commands.record_verdict("r", failed)
    with pytest.raises(BenchError) as refused:
        commands.record_verdict("r", {**failed, "verdict": "pass"})
    assert refused.value.reason == "gate_verdict_final"
    gates = suite.space.published / "gates" / "r.json"
    assert json.loads(gates.read_bytes())["verdict"] == "fail"


@pytest.mark.trace("E18-03", "INV-E18-05", "T-E18-25")
def test_compare_refuses_partial_or_unreported_runs_and_scores_failures_instead_of_dropping_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B5 and M6: compare needs two full reported runs with equal labels; a timed-out case counts as a fail;
    an invalidated run is republished as failed and refused by gate and compare."""
    suite = Suite(tmp_path, monkeypatch)
    honest, _ = suite.full_run(HubScript(warning_level="emergency"), "system_assisted", capsys)
    fabricated, _ = suite.full_run(
        HubScript(warning_level="emergency", fabricate=True), "system_assisted", capsys
    )
    root = ["--published", str(suite.space.published), "--root", str(suite.space.runs)]
    assert suite.cli("compare", *root, honest, fabricated) == 0
    comparison = _last_json(capsys)
    assert comparison["paired_n"] == 2 and comparison["unpaired"] == 0
    assert comparison["prefer"] == "baseline", "a critical gate count rose"
    assert comparison["gate_counts"]["candidate"]["fabricated_accepted_result"] == 2  # type: ignore[index]
    assert (suite.space.published / fabricated / ("comparison_" + honest + ".json")).is_file()
    assert (
        suite.cli(
            "invalidate",
            *root,
            "--run-id",
            fabricated,
            "--reason",
            "grader_unpinned",
            "--evidence",
            "review B2",
        )
        == 0
    )
    manifest = json.loads((suite.space.published / fabricated / "run_manifest.json").read_bytes())
    assert (manifest["state"], manifest["status"]) == ("invalidated", "failed")
    assert manifest["invalidation"] == {"reason": "grader_unpinned", "evidence": ["review B2"]}
    assert suite.cli("compare", *root, honest, fabricated) == 4
    assert "invalidated" in _problem(capsys)["detail"]
    assert suite.cli("gate", *root, "--runs", honest, fabricated) == 4
    assert "invalidated" in _problem(capsys)["detail"]


@pytest.mark.trace("INV-E18-05", "T-E18-25")
def test_compare_values_count_a_timeout_and_refuse_an_ungraded_case() -> None:
    from crew_health_bench.api.gate_commands import paired_value

    def row(status: str, score: float | None, outcomes: list[tuple[str, str]]) -> dict[str, object]:
        listed = [{"check": check, "outcome": outcome} for check, outcome in outcomes]
        return {"status": status, "official_score": score, "outcomes": listed}

    assert paired_value(row("completed", 0.25, [])) == 0.25
    assert paired_value(row("timeout", None, [("A", "not_graded")])) == 0.0
    assert paired_value(row("completed", None, [("A", "pass"), ("B", "fail"), ("C", "not_graded")])) == 0.5
    with pytest.raises(BenchError):
        paired_value(row("completed", None, [("A", "not_graded")]))


@pytest.mark.trace("E18-03", "INV-E18-05", "T-E18-27")
def test_suite_refuses_with_a_typed_reason_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = Suite(tmp_path, monkeypatch)
    plan = suite.plan_input("system_assisted")
    assert suite.suite("plan", "--manifest", str(plan)) == 6
    problem = _problem(capsys)
    assert problem["type"] == "urn:crew-health:bench:harness_unavailable"
    assert "crew_health_harness" in str(problem["detail"])
    assert not suite.space.runs.exists()
    with suite.harness(HubScript(), operations=tuple(sorted(providers_ops()[1:]))):
        assert suite.suite("plan", "--manifest", str(plan)) == 6
        problem = _problem(capsys)
        assert problem["type"] == "urn:crew-health:bench:provider_missing"
        assert problem["detail"] == "evaluation hub lacks: " + providers_ops()[0]
    assert not suite.space.runs.exists()


@pytest.mark.trace("E18-03", "INV-E18-03", "INV-E18-10")
def test_generation_refuses_a_vault_identity_and_a_live_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = Suite(tmp_path, monkeypatch)
    with suite.harness(HubScript(mission_mode="LIVE")) as stand_in:
        assert suite.suite("plan", "--manifest", str(suite.plan_input("system_assisted"))) == 0
        run_id = str(_last_json(capsys)["run_id"])
        assert suite.suite("generate", "--run-id", run_id) == 5
        assert _problem(capsys)["type"] == "urn:crew-health:bench:holdout_key_required"
        assert stand_in.started == []
        monkeypatch.delenv("CHC_EVAL_IDENTITY")
        assert suite.suite("generate", "--run-id", run_id) == 5
        assert _problem(capsys)["type"] == "urn:crew-health:bench:live_hub_refused"
        assert len(stand_in.started) == 1 and stand_in.started[0].server.state.model_requests == []
    assert not (suite.space.runs / run_id / "generations.jsonl").exists()


def providers_ops() -> tuple[str, ...]:
    from crew_health_bench.domain.readiness import DRIVER_OPERATIONS

    return DRIVER_OPERATIONS
