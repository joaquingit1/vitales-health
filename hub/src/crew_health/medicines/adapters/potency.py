"""The potency engine adapter (E32 5.3; AGENTS section 10): pharmacy is consumed only through its public pipeline,
never edited or tuned. The run happens in a child process (the hub environment does not carry numpy; the child uses
the interpreter configured for the pharmacy package), and its JSON result is mapped to LotSummary values."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from crew_health.medicines.domain.model import LotState, LotSummary, Potency

type Json = Mapping[str, object]

_CHILD = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from pharmacy.config import load_bundle\n"
    "from pharmacy import pipeline\n"
    "from pharmacy.types import to_json\n"
    "bundle = load_bundle(Path(sys.argv[1]))\n"
    "inputs = json.loads(sys.stdin.read())\n"
    "sys.stdout.write(json.dumps(to_json(pipeline.assess_inventory(bundle, inputs))))\n"
)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    status: Literal["completed", "failed", "timed_out"]
    result: Json | None
    detail: str


class ChildProcessPotencyEngine:
    """Runs `pharmacy.pipeline.assess_inventory` in a child interpreter with a deadline. Never raises for an engine
    failure: the run is `failed` or `timed_out`, and the last completed assessment stays in use while fresh (DD9)."""

    def __init__(self, python: Path, config_dir: Path, *, timeout_seconds: float, cwd: Path) -> None:
        self.python, self.config_dir, self.timeout, self.cwd = python, config_dir, timeout_seconds, cwd

    def assess(self, inputs: Json) -> RunOutcome:
        try:
            done = subprocess.run(  # noqa: S603 (a fixed interpreter and a constant program)
                [str(self.python), "-c", _CHILD, str(self.config_dir)],
                input=json.dumps(inputs),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RunOutcome("timed_out", None, "deadline passed")
        except OSError as error:
            return RunOutcome("failed", None, f"engine unavailable: {error.__class__.__name__}")
        if done.returncode != 0:
            last = done.stderr.strip().splitlines()[-1:] or ["no detail"]
            return RunOutcome("failed", None, last[0][:300])
        return RunOutcome("completed", cast(Json, json.loads(done.stdout)), "")


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
