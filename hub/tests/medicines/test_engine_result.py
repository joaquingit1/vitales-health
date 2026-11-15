"""A clean exit with a malformed result never replaces the last completed assessment (review B2; E32 5.3, DD9).

Every malformed shape below would have been published, or crashed the completion, before the result was validated:
the run now fails `result_invalid`, the `do_not_use` exclusion of the previous run stays in force and the board says
the current assessment status is unknown."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crew_health.medicines.adapters.engine_result import SCHEMA_DIR
from crew_health.medicines.application.ports import RunOutcome

from .harness import REPO, FakeEngine, Station, build_station, fixture_config, headers
from .test_assessments import _runs, _schedule, _status
from .test_flow import _context, _diagnose, _propose

type Patch = Callable[[dict[str, Any]], object]


def _lot(result: dict[str, Any], lot_id: str) -> dict[str, Any]:
    return next(a for a in result["assessments"] if a["lot_id"] == lot_id)


def _pmz_estimate(result: dict[str, Any]) -> dict[str, Any]:
    estimate: dict[str, Any] = _lot(result, "lot-pmz-tab-001")["estimates"][0]
    return estimate


def _excluded(result: dict[str, Any]) -> dict[str, Any]:
    decision = _lot(result, "lot-pmz-tab-001")["decision"]
    decision["state"] = "do_not_use"
    decision["adjustment"]["eligible"] = False
    return result


def _set(path: Callable[[dict[str, Any]], dict[str, Any]], key: str, value: object) -> Patch:
    def patch(result: dict[str, Any]) -> object:
        path(result)[key] = value
        return result

    return patch


def _drop_pmz(result: dict[str, Any]) -> object:
    result["assessments"] = [a for a in result["assessments"] if a["lot_id"] != "lot-pmz-tab-001"]
    return result


def _duplicate(result: dict[str, Any]) -> object:
    result["assessments"].append(json.loads(json.dumps(_lot(result, "lot-apap-001"))))
    return result


def _foreign_lot(result: dict[str, Any]) -> object:
    extra = json.loads(json.dumps(_lot(result, "lot-apap-001")))
    for node in (extra, extra["decision"], *extra["estimates"]):
        node["lot_id"] = "lot-ghost-001"
    result["assessments"].append(extra)
    return result


def _no_median(result: dict[str, Any]) -> object:
    estimate = _pmz_estimate(result)
    estimate["quantiles"] = [q for q in estimate["quantiles"] if q["probability"] != 0.5]
    return result


def _unordered(result: dict[str, Any]) -> object:
    quantiles = _pmz_estimate(result)["quantiles"]
    quantiles[0]["value"], quantiles[-1]["value"] = quantiles[-1]["value"], quantiles[0]["value"]
    return result


def _raw(number: str) -> Patch:
    """A raw number in the serialized result (`NaN` and `1e400` cannot be built as JSON values)."""

    def patch(result: dict[str, Any]) -> object:
        _pmz_estimate(result)["quantiles"][0]["value"] = "SENTINEL"
        return json.dumps(result).replace('"SENTINEL"', number, 1)

    return patch


MALFORMED: Mapping[str, Patch] = {
    "empty object": lambda r: {},
    "array": lambda r: [],
    "not json": lambda r: "{not json",
    "no assessments": _set(lambda r: r, "assessments", []),
    "one lot missing": _drop_pmz,
    "a lot twice": _duplicate,
    "a lot not in the bundle": _foreign_lot,
    "estimate missing": _set(lambda r: _lot(r, "lot-pmz-tab-001"), "estimates", []),
    "another ingredient": _set(_pmz_estimate, "ingredient_id", "acetaminophen"),
    "NaN": _raw("NaN"),
    "overflow to infinity": _raw("1e400"),
    "no median": _no_median,
    "quantiles out of order": _unordered,
    "another as_of": _set(lambda r: r, "as_of_utc", "2020-01-01T00:00:00Z"),
    "unknown state": _set(lambda r: _lot(r, "lot-pmz-tab-001")["decision"], "state", "fine"),
    "eligibility as text": _set(
        lambda r: _lot(r, "lot-pmz-tab-001")["decision"]["adjustment"], "eligible", "yes"
    ),
    "decision for another lot": _set(
        lambda r: _lot(r, "lot-pmz-tab-001")["decision"], "lot_id", "lot-apap-001"
    ),
    "estimate without quantiles": _set(_pmz_estimate, "quantiles", None),
}


@dataclass
class Shaped(FakeEngine):
    """The recorded run, reshaped by `patch` (a dict is serialized, a string is sent as it is)."""

    patch: Patch = field(default=lambda r: r)

    def assess(self, bundle: Mapping[str, bytes], inputs: bytes) -> RunOutcome:
        recorded = super().assess(bundle, inputs)
        assert recorded.result is not None
        shaped = self.patch(json.loads(recorded.result))
        body = shaped if isinstance(shaped, str) else json.dumps(shaped)
        return RunOutcome("completed", body.encode(), None, "")


@pytest.mark.trace("E32-5.3", "DD9", "INV-M2", "REVIEW-B2")
@pytest.mark.parametrize("shape", sorted(MALFORMED))
def test_a_malformed_result_fails_the_run_and_keeps_the_previous_exclusion(
    tmp_path: Path, shape: str
) -> None:
    st: Station = build_station(tmp_path, config_dir=fixture_config(tmp_path / "config-fixture-lots"))
    st.use_engine(Shaped(patch=_excluded))
    _context(st)
    assert st.drain() == 1
    (first,) = _runs(st)
    assert _status(st, first) == ("completed", None)

    st.use_engine(Shaped(patch=lambda r: MALFORMED[shape](_excluded(r))))
    _schedule(st)
    assert st.drain() == 1
    second = _runs(st)[-1]
    assert _status(st, second) == ("failed", "result_invalid")

    board = st.client.get("/v1/medicines/lots", headers=headers()).json()
    assert board["assessment"]["run_id"] == first and board["assessment"]["status"] == "unknown"
    (pmz,) = [lot for lot in board["lots"] if lot["lot_id"] == "lot-pmz-tab-001"]
    assert pmz["state"] == "do_not_use"
    refused = _propose(st, _diagnose(st, "ICL97"), "promethazine_25_tab")
    assert refused["outcome"] == "refusal" and refused["refusal"]["code"] == "no_usable_lot"  # type: ignore[index]


@pytest.mark.trace("E32-5.3", "AGENTS-10")
def test_the_hub_copy_of_the_engine_result_schema_equals_the_engine_contract() -> None:
    for name in ("inventory-assessment.schema.json", "common.schema.json"):
        assert (SCHEMA_DIR / name).read_bytes() == (REPO / "contracts/pharmacy" / name).read_bytes(), name
