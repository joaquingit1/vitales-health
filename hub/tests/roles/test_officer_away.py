"""V85 to V89 (the E31 parts): with the medical officers away, the helper row proposes a trained crewmate and says why."""

from __future__ import annotations

from pathlib import Path

import pytest

from .harness import Harness, assignments, person_of, texts


@pytest.mark.trace("V89", "D37", "R54")
def test_officers_on_walk_propose_trained_crewmate_with_reason(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.practice("tomas", "pulse_check_wrist")
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    h.open_plan(h.plan_run())
    status, page = h.get("/v1/plan-runs/prn_icl10_mara_1/roles", "mara")
    assert status == 200, page
    (row,) = assignments(page)
    assert person_of(row) == "tomas"
    assert row["role_id"] == "lead_helper"
    assert row["task_text"] == "Pulse check at the wrist"
    assert row["reason_text"] == "Pulse check practiced day 12"
    assert texts(row["context_reasons"]) == [
        "Ilya Brandt, medical officer, is on a surface walk until 11:40",
        "Sofia Reyes, medical officer, is on a surface walk until 11:40",
    ]
