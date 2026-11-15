"""The potency worker and engine adapter (E32 5.3; AGENTS section 10): pharmacy is consumed only through its public
pipeline, never edited or tuned. Each run happens in a child interpreter (the hub environment carries no numpy) that
runs `potency_child.py` by path with `-I`: nice 10 before numpy loads, its peak memory sampled every
`medicines.memory_sample_ms` against `medicines.worker_memory_limit_bytes`, and killed by the parent at the
`medicines.assessment_timeout_seconds` deadline. The bundle reaches the child as files written from the stored
artifacts into a private temporary directory and re-hashed there. Nothing here raises for an engine failure: every
outcome is a RunOutcome."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from crew_health.medicines.application.ports import RunOutcome
from crew_health.medicines.domain.model import LotState, LotSummary, Potency

type Json = Mapping[str, object]

CHILD = Path(__file__).with_name("potency_child.py")
# pharmacy.config.BUNDLE_FILES (member to file name), copied: the hub process never imports pharmacy (its environment
# does not carry it); a test keeps the copy equal to the original.
BUNDLE_FILES: Mapping[str, str] = {
    "sources": "sources.json",
    "engine-settings": "engine-settings.json",
    "reaction-classes": "reaction-classes.json",
    "lfer-constants": "lfer-constants.json",
    "radiation-chemistry": "radiation-chemistry.json",
    "packages": "packages.json",
    "storage-zones": "storage-zones.json",
    "formulary": "formulary.json",
    "stability-models": "stability-models.json",
    "environment-profiles": "environment-profiles.json",
    "lots": "lots.json",
}
_ENGINE_EXIT, _MEMORY_EXIT = 2, 3


def _write_bundle(directory: Path, bundle: Mapping[str, bytes]) -> str | None:
    """Writes each member's file and reads it back: None when every file hashes as stored, else the member."""
    for member, data in bundle.items():
        target = directory / BUNDLE_FILES[member]
        target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
            return member
    return None


class PotencyWorker:
    """Implements medicines.application.ports.PotencyEngine. `python` is the interpreter that carries pharmacy and
    its numerical stack (`medicines.engine_python`); None means no engine on this hub (`engine_unavailable`)."""

    def __init__(
        self,
        python: Path | None,
        *,
        timeout_seconds: float,
        memory_limit_bytes: int,
        memory_sample_ms: int,
    ) -> None:
        self.python, self.timeout = python, timeout_seconds
        self.memory_limit, self.sample_ms = memory_limit_bytes, memory_sample_ms

    def assess(self, bundle: Mapping[str, bytes], inputs: bytes) -> RunOutcome:
        return self._run("assess", bundle, b'{"inputs":' + inputs + b"}")

    def replay(self, bundle: Mapping[str, bytes], inputs: bytes, recorded: bytes) -> RunOutcome:
        return self._run("replay", bundle, b'{"inputs":' + inputs + b',"recorded":' + recorded + b"}")

    def check(self, bundle: Mapping[str, bytes]) -> RunOutcome:
        return self._run("check", bundle, b"{}")

    def summaries(self, run_id: str, result: bytes) -> Mapping[str, LotSummary]:
        return summaries(run_id, cast(Json, json.loads(result)))

    def _run(self, mode: str, bundle: Mapping[str, bytes], payload: bytes) -> RunOutcome:
        if self.python is None or not self.python.is_file():
            return RunOutcome("failed", None, "engine_unavailable", "no engine interpreter configured")
        with tempfile.TemporaryDirectory(prefix="medicines-run-") as raw:
            directory = Path(raw)
            os.chmod(directory, 0o700)
            changed = _write_bundle(directory, bundle)
            if changed is not None:
                return RunOutcome("failed", None, "artifact_corrupt", f"{changed} changed on disk")
            argv = [
                str(self.python),
                "-I",
                str(CHILD),
                mode,
                str(directory),
                str(self.memory_limit),
                str(self.sample_ms),
                repr(self.timeout),
            ]
            try:
                child = subprocess.Popen(  # noqa: S603 (a configured interpreter and this module's own child)
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=directory,
                )
            except OSError as error:
                return RunOutcome("failed", None, "engine_unavailable", type(error).__name__)
            try:
                out, err = child.communicate(payload, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                return RunOutcome("timed_out", None, "timed_out", f"past {self.timeout} s")
        return _outcome(child.returncode, out, err)


class PharmacyBundleCheck:
    """Implements medicines.application.ports.BundleCheck: every member present and JSON here, then pharmacy's own
    `load_bundle` in the worker's child process. With no engine interpreter on the hub, only the first part runs (the
    medicines documents still check the formulary, lots and zones they read)."""

    def __init__(self, worker: PotencyWorker) -> None:
        self.worker = worker

    def check(self, files: Mapping[str, bytes]) -> str | None:
        if set(files) != set(BUNDLE_FILES):
            return "the bundle needs exactly the members " + ", ".join(sorted(BUNDLE_FILES))
        for member, data in files.items():
            try:
                json.loads(data)
            except ValueError, UnicodeError:
                return f"{member} is not JSON"
        if self.worker.python is None:
            return None
        outcome = self.worker.check(files)
        return None if outcome.status == "completed" else f"{outcome.error_code}: {outcome.detail}"


def _outcome(code: int, out: bytes, err: bytes) -> RunOutcome:
    if code == 0:
        return RunOutcome("completed", out, None, "")
    if code == _MEMORY_EXIT:
        return RunOutcome("failed", None, "memory_limit", "resident memory over the limit")
    try:
        reason = cast(Json, json.loads(err.decode("utf-8", "replace").strip().splitlines()[-1]))
    except ValueError, IndexError:
        reason = {}
    if code == _ENGINE_EXIT:
        return RunOutcome(
            "failed", None, "engine_error", f"{reason.get('error')}: {reason.get('detail', '')}"
        )
    return RunOutcome("failed", None, "engine_crashed", f"exit status {code}")


def _fraction(value: object) -> Decimal:
    return Decimal(str(value)) / Decimal(100)


def _instant(text: object) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _list(value: object) -> Sequence[Json]:
    return cast(Sequence[Json], value) if isinstance(value, list) else ()


def summaries(run_id: str, inventory: Json) -> dict[str, LotSummary]:
    """Map an InventoryAssessment (contracts/pharmacy/inventory-assessment.schema.json) to one LotSummary per lot.
    Potency uses the outermost reported quantiles as low and high and the 0.5 quantile as median, as fractions of
    label claim; a lot whose estimate lacks the median is left without potency (never a guessed value)."""
    out: dict[str, LotSummary] = {}
    for assessment in _list(inventory.get("assessments")):
        decision = cast(Json, assessment["decision"])
        adjustment = cast(Json, decision["adjustment"])
        potency: dict[str, Potency] = {}
        declared = False
        for estimate in _list(assessment.get("estimates")):
            quantiles = sorted(
                _list(estimate.get("quantiles")), key=lambda q: float(cast(float, q["probability"]))
            )
            median = [q for q in quantiles if float(cast(float, q["probability"])) == 0.5]
            if quantiles and median:
                potency[str(estimate["ingredient_id"])] = Potency(
                    low=_fraction(quantiles[0]["value"]),
                    median=_fraction(median[0]["value"]),
                    high=_fraction(quantiles[-1]["value"]),
                )
            exposure = cast(Json, estimate.get("exposure") or {})
            for row in _list(exposure.get("coverage")):
                if (
                    float(cast(float, row.get("declared_s", 0))) > 0
                    or float(cast(float, row.get("gap_s", 0))) > 0
                ):
                    declared = True
        pins = cast(Json, decision.get("pins") or {})
        out[str(assessment["lot_id"])] = LotSummary(
            run_id=run_id,
            as_of=_instant(decision["as_of_utc"]),
            state=cast(LotState, decision["state"]),
            potency=potency,
            adjustment_eligible=bool(adjustment["eligible"]),
            assay_used=bool(_list(adjustment.get("assay_ids_used"))),
            custody_declared_or_gapped=declared,
            engine_version=str(pins.get("engine_version") or "unknown"),
            alternative_lot_ids=tuple(
                str(x) for x in cast(list[object], decision.get("alternative_lot_ids") or [])
            ),
            alternative_product_ids=tuple(
                str(x) for x in cast(list[object], decision.get("alternative_product_ids") or [])
            ),
        )
    return out
