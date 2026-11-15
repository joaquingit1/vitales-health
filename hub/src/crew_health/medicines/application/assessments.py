"""RunAssessment, ImportPharmacyBundle and artifact retention (E32 4.4, 5.2, 5.3).

A run is a platform task: the supervisor claims it, calls `AssessmentHandler.execute` outside any transaction (the
start is committed first: the bundle in use and the inputs artifact, built from this partition's records at the hub
clock), the worker runs the engine in its child process, and `apply` stores the outcome in the task's completing
transaction: the result artifact and the mapped lot summaries, or the failure with its code. The engine never changes
a stored result; a later run supersedes it only by completing. Pruning removes only the inputs of unreferenced runs
past `medicines.retained_unreferenced_runs`, never a run a proposal names."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.tasks import TaskLease, TaskResult
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.medicines.application import events, scheduling
from crew_health.medicines.application.codec import summary_json
from crew_health.medicines.application.ports import (
    ArtifactCorrupt,
    Partition,
    RunOutcome,
    RunResult,
    RunRow,
    RunStart,
    View,
)
from crew_health.medicines.application.use_cases import Deps, Documents

WORKER = Actor("engine", "medicines.worker")
INPUTS_SCHEMA = "pharmacy/inputs.schema.json"  # the const of contracts/pharmacy/inputs.schema.json
_HELD_OUTCOMES = 8  # outcomes between execute and apply; one run at a time, so one is normally held

type DocumentsOf = Callable[[Mapping[str, bytes]], Documents]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- bundles ----


def manifest(files: Mapping[str, bytes]) -> bytes:
    """The bundle manifest: member name to file SHA-256; its own hash is the bundle hash."""
    return _canonical({member: hashlib.sha256(data).hexdigest() for member, data in sorted(files.items())})


def bundle_files(deps: Deps, view: View, bundle_sha256: str) -> dict[str, bytes]:
    """The members of a stored bundle, each re-hashed on read (ArtifactCorrupt on any mismatch)."""
    raw = deps.store.artifact(view, bundle_sha256)
    if raw is None:
        raise ArtifactCorrupt(bundle_sha256)
    out: dict[str, bytes] = {}
    for member, sha in json.loads(raw).items():
        data = deps.store.artifact(view, sha)
        if data is None:
            raise ArtifactCorrupt(sha)
        out[member] = data
    return out


def _lots(files: Mapping[str, bytes]) -> dict[str, Mapping[str, object]]:
    lots = json.loads(files["lots"], parse_float=Decimal).get("lots") or []
    return {str(lot["lot_id"]): lot for lot in lots}


def _guard(deps: Deps, uow: UnitOfWork, files: Mapping[str, bytes], active: str | None) -> None:
    """M-A1: every lot with movements still exists with the same product and unit, and its quantity covers the net
    movements of every partition (a lot never goes below zero by a bundle import)."""
    new = _lots(files)
    old = {} if active is None else _lots(bundle_files(deps, uow, active))
    for (mission, mode, environment, lot_id), net in sorted(
        deps.store.net_by_partition(uow, limit=deps.limits.max_rows).items()
    ):
        lot = new.get(lot_id)
        where = f"{lot_id} in {mission}/{mode}/{environment}"
        if lot is None:
            raise ProblemError(
                ErrorCode.CONFIG_INVALID, f"M-A1: {where} has movements and is gone", retryable=False
            )
        quantity = lot["quantity"]
        before = old.get(lot_id)
        if before is not None and (
            before["product_id"] != lot["product_id"] or before["quantity"]["unit"] != quantity["unit"]  # type: ignore[index]
        ):
            raise ProblemError(
                ErrorCode.CONFIG_INVALID, f"M-A1: {where} changed product or unit", retryable=False
            )
        if Decimal(str(quantity["value"])) + net < 0:  # type: ignore[index]
            raise ProblemError(
                ErrorCode.CONFIG_INVALID,
                f"M-A1: {where} holds {quantity['value']} below its net movements {net}",  # type: ignore[index]
                retryable=False,
            )


def import_bundle(
    deps: Deps, actor: Actor, files: Mapping[str, bytes], documents_of: DocumentsOf
) -> Documents:
    """ImportPharmacyBundle: pharmacy's loader accepts the files, the medicines documents check against them, guard
    M-A1 holds, then every file and the manifest are stored and the bundle activated. Any refusal raises
    (CONFIG_INVALID or DocumentsInvalid) and the last good bundle stays active."""
    reason = deps.bundle_check.check(files)
    if reason is not None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "pharmacy bundle refused: " + reason, retryable=False)
    documents = documents_of(files)
    listed = manifest(files)
    bundle_sha = hashlib.sha256(listed).hexdigest()
    with deps.uow.begin(actor=actor, correlation_id=deps.ids.new("cor")) as uow:
        active = deps.store.active_bundle(uow)
        if active == bundle_sha:
            return documents
        _guard(deps, uow, files, active)
        for data in files.values():
            deps.store.put_artifact(uow, "bundle_file", data)
        deps.store.put_artifact(uow, "bundle_manifest", listed)
        deps.store.add_bundle(uow, bundle_sha, actor.id)
        deps.store.activate_bundle(uow, bundle_sha, actor.id)
    return documents


def active_documents(deps: Deps, documents_of: DocumentsOf) -> Documents | None:
    """The documents over the active stored bundle (start-up when the configured bundle is refused)."""
    with deps.snapshots.snapshot() as view:
        active = deps.store.active_bundle(view)
        return None if active is None else documents_of(bundle_files(deps, view, active))


# ---- inputs ----


def _number(value: object) -> float:
    return float(Decimal(str(value)))


def build_inputs(deps: Deps, view: View, mission_id: str, origin_mode: str, as_of: datetime) -> bytes:
    """EngineInputsDoc (E32 5.3) from this mission and mode only (INV-M7): assays and inspections at or before
    `as_of`. Sensor series: none until a habitat reading port and `channel_bindings` exist (every binding list in
    config/medicines.json is empty), so sensed zones show gaps and never compensate."""
    partition = Partition(mission_id, origin_mode, "")
    assays = []
    for row in deps.repository.records(view, "assay", partition, limit=deps.limits.max_rows):
        measured = datetime.fromisoformat(str(row.body["measured_at"]))
        if measured > as_of:
            continue
        assays.append(
            {
                "assay_id": row.record_id,
                "lot_id": row.body["lot_id"],
                "ingredient_id": row.body["ingredient_id"],
                "measured_at_utc": _utc(measured),
                "measured_at_s": measured.timestamp(),
                "measurand": row.body["measurand"],
                "value_percent": _number(row.body["value_percent"]),
                "sd_percent": _number(row.body["sd_percent"]),
                "site": row.body["site"],
                "method": row.body["method"],
                "laboratory": row.body["laboratory"],
                "note": row.body.get("note") or "",
            }
        )
    inspections = []
    for row in deps.repository.records(view, "inspection", partition, limit=deps.limits.max_rows):
        if row.at > as_of:
            continue
        inspections.append(
            {
                "inspection_id": row.record_id,
                "lot_id": row.body["lot_id"],
                "inspected_at_utc": _utc(row.at),
                "inspected_at_s": row.at.timestamp(),
                "finding": row.body["finding"],
                "inspector_role": row.body.get("inspector_role") or "crew",
                "note": row.body.get("note") or "",
            }
        )
    return _canonical(
        {
            "schema": INPUTS_SCHEMA,
            "as_of_utc": _utc(as_of),
            "sensor_series": [],
            "assays": assays,
            "inspections": inspections,
        }
    )


# ---- one run ----


@dataclass(frozen=True, slots=True)
class Started:
    run: RunRow
    start: RunStart
    inputs: bytes


def _finish(deps: Deps, uow: UnitOfWork, run_id: str, status: str, error_code: str | None) -> None:
    deps.store.add_run_event(uow, run_id, status, error_code)
    events.assessment_changed(uow, run_id, status, error_code)


def start(deps: Deps, run_id: str, actor: Actor) -> Started | None:
    """Commits the start of a queued run (bundle, inputs artifact, `running`); None when the run is already
    terminal (interrupted at start-up, say) or failed here (`input_limit_exceeded`, no bundle)."""
    with deps.uow.begin(actor=actor, correlation_id=deps.ids.new("cor")) as uow:
        run = deps.store.run(uow, run_id)
        if run is None or run.start is not None or deps.store.run_status(uow, run_id)[0] != "queued":
            return None
        bundle = deps.store.active_bundle(uow)
        if bundle is None:
            _finish(deps, uow, run_id, "failed", "no_bundle")
            return None
        inputs = build_inputs(deps, uow, run.mission_id, run.origin_mode, uow.now)
        if len(inputs) > deps.limits.max_inputs_bytes:
            _finish(deps, uow, run_id, "failed", "input_limit_exceeded")
            return None
        begun = RunStart(uow.now, bundle, deps.store.put_artifact(uow, "inputs", inputs))
        deps.store.start_run(uow, run_id, begun)
        _finish(deps, uow, run_id, "running", None)
        return Started(run, begun, inputs)


def run_engine(deps: Deps, started: Started) -> RunOutcome:
    """Outside any transaction: the stored bundle, re-hashed, then the worker."""
    try:
        with deps.snapshots.snapshot() as view:
            files = bundle_files(deps, view, started.start.bundle_sha256)
    except ArtifactCorrupt as error:
        return RunOutcome("failed", None, "artifact_corrupt", str(error))
    return deps.engine.assess(files, started.inputs)


def record_outcome(deps: Deps, uow: UnitOfWork, run_id: str, outcome: RunOutcome) -> None:
    """The terminal event of a running run, in the caller's transaction; a run already terminal stays as it is."""
    if deps.store.run_status(uow, run_id)[0] != "running":
        return
    run = deps.store.run(uow, run_id)
    if outcome.status != "completed" or outcome.result is None or run is None:
        _finish(
            deps,
            uow,
            run_id,
            outcome.status if outcome.status != "completed" else "failed",
            outcome.error_code,
        )
        return
    try:
        lots = deps.engine.summaries(run_id, outcome.result)
    except ValueError, KeyError, TypeError, ArithmeticError:
        _finish(deps, uow, run_id, "failed", "result_unmapped")
        return
    result_sha = deps.store.put_artifact(uow, "result", outcome.result)
    version = next((s.engine_version for s in lots.values()), "unknown")
    summaries = json.dumps(
        {k: summary_json(v) for k, v in sorted(lots.items())},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    deps.store.add_result(uow, RunResult(run_id, result_sha, version, summaries))
    _finish(deps, uow, run_id, "completed", None)
    prune(deps, uow, run.mission_id, run.origin_mode)


def prune(deps: Deps, uow: UnitOfWork, mission_id: str, origin_mode: str) -> tuple[str, ...]:
    """E32 5.2 retention: inputs of unreferenced runs older than the newest `retained_unreferenced_runs` ones are
    deleted (recorded in med_artifact_prune first); a hash any referenced or retained run uses is never deleted."""
    referenced = deps.store.referenced_runs(uow, limit=deps.limits.max_rows)
    keep: set[str] = set()
    for run_id in sorted(referenced):
        run = deps.store.run(uow, run_id)
        if run is not None and run.start is not None:
            keep.add(run.start.inputs_sha256)
    older: list[RunRow] = []
    seen = 0
    for run in deps.store.runs(uow, mission_id, origin_mode, limit=deps.limits.max_rows):
        if run.start is None or run.run_id in referenced:
            continue
        seen += 1
        if seen <= deps.limits.retained_unreferenced_runs:
            keep.add(run.start.inputs_sha256)
        else:
            older.append(run)
    pruned = []
    for run in older:
        sha = run.start.inputs_sha256 if run.start is not None else ""
        if sha not in keep and deps.store.has_artifact(uow, sha):
            deps.store.prune_artifact(uow, sha, run.run_id)
            pruned.append(run.run_id)
    return tuple(pruned)


# ---- the task handler ----


class AssessmentHandler:
    """kernel.tasks.TaskHandler for `medicines.assessment`. The outcome travels from `execute` to `apply` in memory,
    keyed by task and lease generation; after a restart there is none, and the start-up recovery has already marked
    the run interrupted."""

    def __init__(self, deps_of: Callable[[], Deps]) -> None:
        self._deps = deps_of
        self._held: OrderedDict[tuple[str, int], RunOutcome] = OrderedDict()

    def execute(self, lease: TaskLease) -> TaskResult:
        deps = self._deps()
        run_id = lease.task.input_ref
        started = start(deps, run_id, lease.task.actor)
        outcome = (
            RunOutcome("failed", None, "not_started", "") if started is None else run_engine(deps, started)
        )
        key = (lease.task.task_id, lease.generation)
        self._held[key] = outcome
        while len(self._held) > _HELD_OUTCOMES:
            self._held.popitem(last=False)
        digest = hashlib.sha256(
            _canonical(
                [outcome.status, outcome.error_code, hashlib.sha256(outcome.result or b"").hexdigest()]
            )
        ).hexdigest()
        return TaskResult(lease.task.task_id, lease.generation, run_id, digest)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        outcome = self._held.pop((lease.task.task_id, lease.generation), None)
        if outcome is None:
            outcome = RunOutcome("failed", None, "interrupted", "the outcome did not survive")
        record_outcome(self._deps(), uow, result.result_ref, outcome)

    def discard(self, result: TaskResult) -> None:
        self._held.pop((result.task_id, result.generation), None)


# ---- start-up and replay ----


def recover(deps: Deps, actor: Actor) -> int:
    """At start-up every run still queued or running gets `interrupted` and one run is scheduled (E32 5.2)."""
    with deps.uow.begin(actor=actor, correlation_id=deps.ids.new("cor")) as uow:
        unfinished = deps.store.unfinished_runs(uow, limit=deps.limits.max_rows)
        for run_id in unfinished:
            _finish(deps, uow, run_id, "interrupted", None)
        facts = deps.mission.current(uow)
        if deps.tasks is not None and facts is not None:
            scheduling.schedule(
                deps.store,
                deps.tasks,
                deps.ids,
                deps.process_epoch,
                uow,
                mission_id=facts.mission_id,
                origin_mode=facts.mission_mode,
                trigger="startup",
            )
        return len(unfinished)


@dataclass(frozen=True, slots=True)
class Replay:
    run_id: str
    status: str  # reproduced, differs, replay_unavailable, artifact_corrupt, not_completed, failed
    detail: str


def replay(deps: Deps, run_id: str) -> Replay:
    """`pharmacy.pipeline.replay` over the stored bundle and inputs reproduces a completed run (E32-T-64). A pruned
    run says `replay_unavailable`, never a recomputed answer."""
    with deps.snapshots.snapshot() as view:
        run = deps.store.run(view, run_id)
        result = deps.store.result(view, run_id)
        if run is None or run.start is None or result is None:
            return Replay(run_id, "not_completed", "")
        try:
            inputs = deps.store.artifact(view, run.start.inputs_sha256)
            if inputs is None:
                return Replay(run_id, "replay_unavailable", "inputs pruned")
            recorded = deps.store.artifact(view, result.result_sha256)
            files = bundle_files(deps, view, run.start.bundle_sha256)
        except ArtifactCorrupt as error:
            return Replay(run_id, "artifact_corrupt", str(error))
    if recorded is None:
        return Replay(run_id, "artifact_corrupt", "result missing")
    outcome = deps.engine.replay(files, inputs, recorded)
    if outcome.status != "completed" or outcome.result is None:
        return Replay(run_id, "failed", f"{outcome.error_code}: {outcome.detail}")
    match = bool(json.loads(outcome.result).get("match"))
    return Replay(run_id, "reproduced" if match else "differs", "")
