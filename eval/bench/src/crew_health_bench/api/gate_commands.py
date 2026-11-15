"""`chc-bench gate` and `chc-bench compare` over published run artifacts (design 3.8, 4.1, 6.2, INV-E18-12).

Both read only `results/<run_id>/run_manifest.json` and `case_results.jsonl`, so case references stay salted.
The gate refuses (`gate_input_invalid`, exit 4) unless every run is a full, reported, system-assisted holdout
run of one model release whose four critical checks were graded pass or fail on every case; the detail names
what is missing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.documents import BenchSettings, decode_json, load_document, read_json
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.gate import CRITICAL_CHECKS, GateRun, gate, validate_gate_configuration
from crew_health_bench.domain.documents import integer, number, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.metrics import compare


class PublishedRun:
    """One reported run as its public artifacts show it."""

    def __init__(self, published: Path, run_id: str, *, maximum: int, max_rows: int) -> None:
        self.manifest = read_json(
            vault_path(published, run_id + "/run_manifest.json"), max_file_bytes=maximum
        )
        if self.manifest.get("run_id") != run_id:
            raise BenchError("integrity", instance=run_id, exit_code=6)
        path = vault_path(published, run_id + "/case_results.jsonl")
        try:
            with path.open("rb") as stream:
                raw = stream.read(maximum * max_rows + 1)
        except OSError as error:
            raise BenchError("target_unreadable", instance=run_id, exit_code=6) from error
        lines = raw.splitlines()
        if len(raw) > maximum * max_rows or len(lines) > max_rows:
            raise BenchError("integrity", instance=run_id, exit_code=6)
        self.rows = tuple(decode_json(line) for line in lines)
        self.run_id = run_id

    def key(self, row: Mapping[str, object]) -> str:
        return text(row["case_ref"]) + ":" + str(integer(row["repeat"]))

    def outcomes(self, row: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
        values = row.get("outcomes")
        if not isinstance(values, list):
            raise BenchError("integrity", instance=self.run_id, exit_code=6)
        return tuple((text(object_map(item)["check"]), text(object_map(item)["outcome"])) for item in values)

    def gate_run(self) -> GateRun:
        manifest = self.manifest
        return GateRun(
            self.run_id,
            text(manifest["release_id"]),
            text(manifest["case_set_sha256"]),
            text(manifest["partition"]),
            text(manifest["completeness"]),
            text(manifest["track"]),
            text(manifest["state"]),
            tuple((self.key(row), text(row["status"])) for row in self.rows),
            tuple(
                (self.key(row), check, outcome) for row in self.rows for check, outcome in self.outcomes(row)
            ),
        )


def _why_not(run: PublishedRun) -> str:
    """The first reason a run cannot feed the gate, in words, or "" (INV-E18-12, design 3.8)."""
    manifest = run.manifest
    expected = {
        "partition": "holdout",
        "completeness": "full",
        "track": "system_assisted",
        "state": "reported",
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            return run.run_id + ": " + field + " is " + str(manifest.get(field)) + ", the gate needs " + value
    for _, check in CRITICAL_CHECKS:
        ungraded = sum(
            dict(run.outcomes(row)).get(check) not in ("pass", "fail") or row.get("status") != "completed"
            for row in run.rows
        )
        if ungraded:
            return (
                run.run_id
                + ": "
                + check
                + " not graded in "
                + str(ungraded)
                + " of "
                + str(len(run.rows))
                + " cases"
            )
    return ""


class GateCommands:
    def __init__(self, config_dir: Path, settings: BenchSettings, published: Path) -> None:
        self.config, self.published = config_dir.resolve(), published
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.max_files = settings.integer("bench.scan.max_files")
        self.schemas = ArtifactSchemas(self.config.parent / "schemas", self.maximum)

    def _runs(self, run_ids: list[str]) -> tuple[PublishedRun, ...]:
        if not run_ids or len(set(run_ids)) != len(run_ids):
            raise BenchError("gate_input_invalid")
        return tuple(
            PublishedRun(self.published, run_id, maximum=self.maximum, max_rows=self.max_files)
            for run_id in run_ids
        )

    def _write(self, relative: Path, document: Mapping[str, object]) -> None:
        with FileArtifactStore(
            self.published, max_line_bytes=self.maximum, max_records=self.max_files
        ) as store:
            store.write_atomic(relative, canonical_json(document))

    def gate(self, run_ids: list[str]) -> tuple[dict[str, object], int]:
        suites = load_document(self.config / "suites.json", max_file_bytes=self.maximum)
        validate_gate_configuration(object_map(suites["release_gates"]))
        runs = self._runs(run_ids)
        for run in runs:
            reason = _why_not(run)
            if reason:
                raise BenchError("gate_input_invalid", instance=run.run_id, detail=reason)
        inputs = tuple(run.gate_run() for run in runs)
        required = {key: tuple(check for _, check in CRITICAL_CHECKS) for key, _ in inputs[0].statuses}
        verdict = decode_json(canonical_json(asdict(gate(inputs, required=required))))
        self.schemas.validate("gate", verdict)
        for run_id in text_list(verdict["run_ids"]):
            self._write(Path(run_id) / "gate.json", verdict)
        return verdict, 0 if verdict["verdict"] == "pass" else 3

    def compare(self, baseline_id: str, candidate_id: str) -> dict[str, object]:
        plan = load_document(self.config / "healthbench-plan.json", max_file_bytes=self.maximum)
        baseline, candidate = self._runs([baseline_id, candidate_id])
        values = {
            run.run_id: {run.key(row): _value(run, row) for row in run.rows} for run in (baseline, candidate)
        }
        keys = sorted(values[baseline_id].keys() & values[candidate_id].keys())
        paired = [
            key
            for key in keys
            if values[baseline_id][key] is not None and values[candidate_id][key] is not None
        ]
        if not paired:
            raise BenchError("case_invalid", detail="no case has a graded value in both runs")
        result = compare(
            tuple(float(values[baseline_id][key] or 0.0) for key in paired),
            tuple(float(values[candidate_id][key] or 0.0) for key in paired),
            case_set_a=text(baseline.manifest["case_set_sha256"]),
            case_set_b=text(candidate.manifest["case_set_sha256"]),
            baseline_gates=_gate_counts(baseline),
            candidate_gates=_gate_counts(candidate),
            seed=int(text(object_map(plan["sample"])["seed"])),
            bootstrap_b=integer(plan["bootstrap_b"]),
            interval_level=float(number(plan["interval_level"])),
        )
        document = {
            **decode_json(canonical_json(asdict(result))),
            "baseline_run_id": baseline_id,
            "candidate_run_id": candidate_id,
            "case_set_sha256": text(baseline.manifest["case_set_sha256"]),
            "paired_n": len(paired),
            "unpaired": len(baseline.rows) - len(paired),
            "interval_label": "our interval",
            "gate_counts": {"baseline": _gate_counts(baseline), "candidate": _gate_counts(candidate)},
            "labels": {run.run_id: _labels(run) for run in (baseline, candidate)},
        }
        self.schemas.validate("comparison", document)
        self._write(Path(candidate_id) / ("comparison_" + baseline_id + ".json"), document)
        return document


def _value(run: PublishedRun, row: Mapping[str, object]) -> float | None:
    """The per-case paired value: the official score, or the share of graded typed checks that passed."""
    if row.get("official_score") is not None:
        return float(number(row["official_score"]))
    outcomes = [outcome for _, outcome in run.outcomes(row) if outcome in ("pass", "fail")]
    return outcomes.count("pass") / len(outcomes) if outcomes else None


def _gate_counts(run: PublishedRun) -> dict[str, int]:
    return {
        name: sum(dict(run.outcomes(row)).get(check) == "fail" for row in run.rows)
        for name, check in CRITICAL_CHECKS
    }


def _labels(run: PublishedRun) -> dict[str, object]:
    return {
        field: run.manifest.get(field)
        for field in ("track", "partition", "completeness", "integrity", "release_id")
    }
