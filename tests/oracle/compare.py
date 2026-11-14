"""Agreement assertions between engine values and the oracle (spec 1.7, 15.2).

Every failure message carries the function, its inputs, both values, the relative
difference and the spec clause, so a disagreement can be reported without rerunning.
"""

from collections.abc import Mapping
from typing import Any

from tests.oracle.precision import (
    ABSOLUTE_TOLERANCE,
    MP,
    RELATIVE_TOLERANCE,
    Mpf,
    agrees,
    relative_difference,
)


def disagreement(function: str, inputs: Mapping[str, Any], engine: Any, oracle: Any, clause: str) -> str:
    try:
        rel = f"{relative_difference(float(engine), oracle):.3e}"
    except TypeError, ValueError, OverflowError:
        rel = "n/a"
    oracle_text = (
        MP.nstr(oracle, 20) if not isinstance(oracle, (bool, int, str, tuple, list)) else repr(oracle)
    )
    return (
        f"ORACLE DISAGREEMENT in {function} (spec {clause}): inputs {dict(inputs)!r}; "
        f"engine {engine!r}; oracle {oracle_text}; relative difference {rel}"
    )


def assert_agrees(
    function: str,
    inputs: Mapping[str, Any],
    engine: float,
    oracle: Mpf,
    clause: str,
    *,
    rel: float = RELATIVE_TOLERANCE,
    abs_tol: float = ABSOLUTE_TOLERANCE,
) -> None:
    """|engine - oracle| <= max(rel |oracle|, abs) (spec 1.7)."""
    assert isinstance(engine, float | int), f"{function} returned {type(engine).__name__}, not a float"
    assert agrees(float(engine), oracle, rel, abs_tol), disagreement(function, inputs, engine, oracle, clause)


def assert_equal(function: str, inputs: Mapping[str, Any], engine: Any, oracle: Any, clause: str) -> None:
    """Exact equality (spec 15.2 exact-equality functions)."""
    assert engine == oracle, disagreement(function, inputs, engine, oracle, clause)
