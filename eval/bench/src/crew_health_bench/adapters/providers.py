"""Where E18 obtains the providers it does not own, and the typed refusal while they are absent (design 9).

- Product suites need E21's harness (`crew_health_harness`, `eval/runner`, draft PR 38) bound to the Protocols
  of `application/harness_ports.py`, and an evaluation hub that mounts every driver operation of design 6.1.
- System-assisted HealthBench needs E13's `BenchmarkAnswerPort` in the evaluation composition (design 4.5).

Neither exists on main, so both resolvers refuse with exit 6 and a detail naming what is missing. They never
fall back to a stand-in and no command produces a result without them. When a provider lands, its binding
replaces the refusal here; the commands and the driver do not change.
"""

from __future__ import annotations

from importlib.util import find_spec

from crew_health_bench.application.harness_ports import ProductHarness
from crew_health_bench.application.model_ports import BenchmarkAnswerPort
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.model import ModelReleaseRef


def _installed(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def product_harness() -> ProductHarness:
    """E21's harness bound to E18's structural ports."""
    if not _installed("crew_health_harness"):
        raise BenchError(
            "harness_unavailable",
            exit_code=6,
            detail="E21's eval/runner (package crew_health_harness, PR 38) is not installed; product suites"
            " cannot run without an evaluation hub (design 4.5, 9)",
        )
    raise BenchError(
        "harness_unavailable",
        exit_code=6,
        detail="crew_health_harness is installed but provides no SimMeasurementAdapter and no binding to"
        " application/harness_ports.py (E21 design section 2, R-E21-4)",
    )


def benchmark_answer_port(release: ModelReleaseRef) -> BenchmarkAnswerPort:
    """E13's `BenchmarkAnswerPort` for `release`, served by the evaluation composition only."""
    raise BenchError(
        "no_evaluation_entry_point",
        exit_code=6,
        instance=release.release_id,
        detail="E13 has not provided BenchmarkAnswerPort (design 4.5, R-E18-8); the system-assisted"
        " HealthBench track cannot run",
    )
