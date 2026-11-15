"""Commutativity and replay properties over the actual command ledger and SQL store."""

from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from .plans_stack import obj, rows, stack


@pytest.mark.trace("T28-13", "T28-39", "SYSTEM-5.8")
@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.permutations(("icl10_note_episodes", "icl10_avoid_triggers", "icl10_add_ecg_report")))
def test_commuting_actions_and_ledger_replay_are_order_independent(
    tmp_path: Path,
    order: Sequence[str],
) -> None:
    with TemporaryDirectory(dir=tmp_path) as directory:
        s = stack(Path(directory))
        opened = s.start()
        current = opened
        for step in order:
            current = s.act(opened, "not_available", step_id=step, key="action_" + step, reason="Unavailable")
        assert current["revision"] == 4
        assert {
            step["step_id"] for step in rows(current["steps"]) if step["state"] == "not_available"
        } == set(order)
        for step in reversed(order):
            assert (
                s.act(opened, "not_available", step_id=step, key="action_" + step, reason="Unavailable")
                == current
            )
        assert len(s.events) == 4
        assert s.query("SELECT count(*) FROM platform_command_ledger") == ((4,),)
        assert "display_name" not in obj(current["opened_by"])
