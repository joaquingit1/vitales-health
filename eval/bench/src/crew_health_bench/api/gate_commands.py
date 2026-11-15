"""`chc-bench gate`, `compare` and `invalidate` over published run artifacts (design 3.8, 4.1, 6.2).

Gate and compare read `results/<run_id>/run_manifest.json` and `case_results.jsonl`, so case references
stay salted, and accept a run only when its private state chain ends in `reported` and records the SHA-256
of every published artifact: an edited or invalidated run never feeds a verdict (INV-E18-12). The gate
refuses (`gate_input_invalid`, exit 4) unless the runs are every reported holdout run of one model release,
each full, system-assisted, measured on the product rather than a stand-in, with its four critical checks
graded pass or fail on every case; the detail names what is missing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid7

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import KernelIdFactory

from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.clock import ProcessClock
from crew_health_bench.adapters.documents import BenchSettings, decode_json, load_document, read_json
from crew_health_bench.adapters.files import bounded_files
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.gate import CRITICAL_CHECKS, GateRun, gate, validate_gate_configuration
from crew_health_bench.domain.documents import integer, number, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.metrics import compare
from crew_health_bench.domain.runs import require_transition

PUBLISHED = ("REPORT.md", "aggregate.json", "case_results.jsonl", "privacy_scan.json", "run_manifest.json")
IDENTITY_FIELDS = (
    "config_release_id",
    "evidence_release_id",
    "evidence_sha256",
    "prompt_sha256",
    "registry_version",
    "tool_catalog_sha256",
)


def run_state(store: FileArtifactStore, run_id: str) -> tuple[str, Mapping[str, object]]:
    """The run's current state and its record, read through the hash-chained private store."""
    previous: str = ""
    record: Mapping[str, object] = {}
    for row in store.read_valid(run_id, "state"):
        current = text(row.get("state"))
        require_transition(previous, current)
        previous, record = current, row
    return previous, record


def stand_in_reason(manifest: Mapping[str, object]) -> str:
    """Why a run measured a stand-in rather than the product, in words, or ""."""
    commit = text(object_map(manifest.get("harness")).get("commit"))
    if commit.startswith("stand-in"):
        return "harness commit " + commit + " is a stand-in"
    for field in IDENTITY_FIELDS:
        value = str(manifest.get(field))
        if value.startswith("none:"):
            return field + " is " + value
    return ""


class PublishedRun:
    """One reported run as its public artifacts show it."""

    def __init__(
        self, published: Path, store: FileArtifactStore, run_id: str, *, maximum: int, max_rows: int
    ) -> None:
        state, record = run_state(store, run_id)
        if state != "reported":
            raise BenchError(
                "gate_input_invalid",
                instance=run_id,
                detail=run_id + ": state is " + (state or "none") + ", only a reported run is read",
            )
        digests = object_map(record.get("artifacts_sha256"))
        for name in PUBLISHED:
            if _digest(vault_path(published, run_id + "/" + name), maximum * max_rows) != digests.get(name):
                raise BenchError(
                    "integrity",
                    instance=run_id,
                    exit_code=6,
                    detail=run_id + ": published " + name + " differs from the reported record",
                )
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


def _digest(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise BenchError(
            "integrity", instance=path.name, exit_code=6, detail=path.name + " unreadable"
        ) from error
    if len(raw) > limit:
        raise BenchError("integrity", instance=path.name, exit_code=6)
    return sha256(raw).hexdigest()


def _why_not(run: PublishedRun) -> str:
    """The first reason a run cannot feed the gate, in words, or "" (INV-E18-12, design 3.8)."""
    manifest = run.manifest
    stand_in = stand_in_reason(manifest)
    if stand_in:
        return run.run_id + ": " + stand_in
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
    def __init__(self, config_dir: Path, settings: BenchSettings, published: Path, root: Path) -> None:
        self.config, self.published, self.root = config_dir.resolve(), published, root
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.max_files = settings.integer("bench.scan.max_files")
        self.schemas = ArtifactSchemas(self.config.parent / "schemas", self.maximum)

    def _store(self, root: Path) -> FileArtifactStore:
        return FileArtifactStore(root, max_line_bytes=self.maximum, max_records=self.max_files)

    def _runs(self, run_ids: list[str]) -> tuple[PublishedRun, ...]:
        if not run_ids or len(set(run_ids)) != len(run_ids):
            raise BenchError("gate_input_invalid")
        with self._store(self.root) as store:
            return tuple(
                PublishedRun(self.published, store, run_id, maximum=self.maximum, max_rows=self.max_files)
                for run_id in run_ids
            )

    def _write(self, relative: Path, document: Mapping[str, object]) -> None:
        with self._store(self.published) as store:
            store.write_atomic(relative, canonical_json(document))

    def _release_runs(self, release_id: str) -> set[str]:
        """Every reported holdout run of the release in the private store (M5)."""
        result: set[str] = set()
        if not self.root.is_dir():
            return result
        with self._store(self.root) as store:
            for path in bounded_files(self.root, max_files=self.max_files):
                if path.name != "state.jsonl" or run_state(store, path.parent.name)[0] != "reported":
                    continue
                source = vault_path(self.published, path.parent.name + "/run_manifest.json")
                if not source.is_file():
                    continue
                manifest = read_json(source, max_file_bytes=self.maximum)
                if (manifest.get("release_id"), manifest.get("partition")) == (release_id, "holdout"):
                    result.add(path.parent.name)
        return result

    def record_verdict(self, release_id: str, verdict: Mapping[str, object]) -> None:
        """Key the verdict by release; a recorded fail is final and is never overwritten (M5)."""
        source = self.published / "gates" / (release_id + ".json")
        if source.is_file():
            existing = read_json(
                vault_path(self.published, "gates/" + release_id + ".json"), max_file_bytes=self.maximum
            )
            if existing.get("verdict") == "fail" and canonical_json(existing) != canonical_json(verdict):
                raise BenchError(
                    "gate_verdict_final",
                    instance=release_id,
                    exit_code=3,
                    detail="release " + release_id + " already failed its gate; a new release is a new gate",
                )
        self._write(Path("gates") / (release_id + ".json"), verdict)

    def gate(self, run_ids: list[str]) -> tuple[dict[str, object], int]:
        suites = load_document(self.config / "suites.json", max_file_bytes=self.maximum)
        validate_gate_configuration(object_map(suites["release_gates"]))
        runs = self._runs(run_ids)
        releases = {text(run.manifest["release_id"]) for run in runs}
        if len(releases) != 1:
            raise BenchError("gate_input_invalid", detail="the gate covers one model release")
        (release_id,) = releases
        missing = sorted(self._release_runs(release_id) - set(run_ids))
        if missing:
            raise BenchError(
                "gate_input_invalid",
                instance=release_id,
                detail="the gate covers every reported holdout run of "
                + release_id
                + "; missing "
                + ", ".join(missing),
            )
        for run in runs:
            reason = _why_not(run)
            if reason:
                raise BenchError("gate_input_invalid", instance=run.run_id, detail=reason)
        inputs = tuple(run.gate_run() for run in runs)
        required = {key: tuple(check for _, check in CRITICAL_CHECKS) for key, _ in inputs[0].statuses}
        verdict = decode_json(canonical_json(asdict(gate(inputs, required=required))))
        self.schemas.validate("gate", verdict)
        self.record_verdict(release_id, verdict)
        for run_id in text_list(verdict["run_ids"]):
            self._write(Path(run_id) / "gate.json", verdict)
        return verdict, 0 if verdict["verdict"] == "pass" else 3

    def compare(self, baseline_id: str, candidate_id: str) -> dict[str, object]:
        plan = load_document(self.config / "healthbench-plan.json", max_file_bytes=self.maximum)
        baseline, candidate = self._runs([baseline_id, candidate_id])
        labels = ("track", "variant", "partition", "integrity")
        for run in (baseline, candidate):
            if run.manifest.get("completeness") != "full":
                raise BenchError(
                    "gate_input_invalid",
                    instance=run.run_id,
                    detail=run.run_id + ": completeness is " + str(run.manifest.get("completeness")),
                )
        if any(baseline.manifest.get(field) != candidate.manifest.get(field) for field in labels):
            raise BenchError("gate_input_invalid", detail="compare needs equal " + ", ".join(labels))
        values = {
            run.run_id: {run.key(row): paired_value(row) for row in run.rows} for run in (baseline, candidate)
        }
        if values[baseline_id].keys() != values[candidate_id].keys():
            raise BenchError("case_set_mismatch")
        paired = sorted(values[baseline_id])
        result = compare(
            tuple(values[baseline_id][key] for key in paired),
            tuple(values[candidate_id][key] for key in paired),
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
            "unpaired": 0,
            "interval_label": "our interval",
            "gate_counts": {"baseline": _gate_counts(baseline), "candidate": _gate_counts(candidate)},
            "labels": {run.run_id: _labels(run) for run in (baseline, candidate)},
        }
        self.schemas.validate("comparison", document)
        self._write(Path(candidate_id) / ("comparison_" + baseline_id + ".json"), document)
        return document

    def invalidate(self, run_id: str, reason: str, evidence: tuple[str, ...]) -> dict[str, object]:
        """`Invalidate` (design 4.1): record the reason, then republish the manifest as `failed` (M6)."""
        if not reason or not evidence or any(not item for item in evidence):
            raise BenchError("case_invalid")
        with self._store(self.root) as store:
            state, record = run_state(store, run_id)
            if state == "invalidated":
                if (record.get("reason"), record.get("evidence")) != (reason, list(evidence)):
                    raise BenchError("invalid_transition", instance=run_id)
            else:
                require_transition(state, "invalidated")
                clock = ProcessClock(KernelIdFactory(new_uuid_hex=lambda: uuid7().hex).new("epoch"))
                store.append(
                    run_id,
                    "state",
                    {
                        "state": "invalidated",
                        "at": clock.now().isoformat(),
                        "reason": reason,
                        "evidence": list(evidence),
                    },
                )
        source = vault_path(self.published, run_id + "/run_manifest.json")
        if source.is_file():
            manifest = read_json(source, max_file_bytes=self.maximum)
            manifest.update(
                state="invalidated",
                status="failed",
                invalidation={"reason": reason, "evidence": list(evidence)},
            )
            self.schemas.validate("run-manifest", manifest)
            self._write(Path(run_id) / "run_manifest.json", manifest)
        return {"run_id": run_id, "state": "invalidated"}


def paired_value(row: Mapping[str, object]) -> float:
    """The per-case paired value; a failed, timed-out or unrun case is a fail, never dropped (B5)."""
    if row.get("official_score") is not None:
        return float(number(row["official_score"]))
    values = row.get("outcomes")
    if not isinstance(values, list):
        raise BenchError("integrity", exit_code=6)
    if row.get("status") != "completed":
        return 0.0
    outcomes = [text(object_map(item)["outcome"]) for item in values]
    graded = [outcome for outcome in outcomes if outcome in ("pass", "fail")]
    if not graded:
        raise BenchError("gate_input_invalid", detail="a completed case has no graded value")
    return graded.count("pass") / len(graded)


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
