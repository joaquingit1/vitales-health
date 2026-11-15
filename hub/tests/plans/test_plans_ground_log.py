"""EI1/EI2: link state belongs to the ground row and the audit pin only."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal

import pytest

from crew_health.kernel.errors import StorageUnavailable
from crew_health.plans.application.ground_log import GroundLine, LinkSnapshot
from crew_health.plans.application.release import UiCopy
from crew_health.plans.application.views import PlanViews

from .plans_stack import NOW, ROOT, load_json, rows, stack


class Reader:
    def __init__(self, status: Literal["up", "delayed", "down"] = "down") -> None:
        self.snapshot = LinkSnapshot(status, datetime.fromisoformat("2026-11-14T11:20:40Z"), None, True, NOW)
        self.error: Exception | None = None

    def current(self) -> LinkSnapshot:
        if self.error is not None:
            raise self.error
        return self.snapshot


class Words:
    def render(self, now: datetime, until: datetime) -> str:
        return "25 hours"


@pytest.mark.trace("EI2", "T28-24", "PR-I5")
def test_link_states_only_change_the_ground_row_and_open_audit_pin(tmp_path: Path) -> None:
    s = stack(tmp_path)
    reader = Reader()
    s.world = replace(s.world, ground_line=GroundLine(reader, Words()))
    baseline: dict[str, object] | None = None
    for status in ("down", "delayed", "up"):
        reader.snapshot = replace(reader.snapshot, status=status)
        result = PlanViews(s.world).condition_plan(s.session, "ICL10", None)
        ground = rows(result["stages"])[-1]
        example = load_json(
            ROOT
            / (
                "contracts/api/examples/plans/view-icl10-lunar-link-up.json"
                if status == "up"
                else "contracts/api/examples/plans/view-icl10-lunar.json"
            )
        )
        expected = rows(example["stages"])[-1]
        assert ground["link_text"] == expected["link_text"]
        assert ground["leaves_at"] == expected["leaves_at"]
        del ground["link_text"], ground["leaves_at"]
        if baseline is not None:
            assert result == baseline
        baseline = result
        pin = s.world.ground_line.at_open() if s.world.ground_line else None
        assert pin is not None and pin.status == status
        assert pin.gap_kind == (None if status == "up" else "unknown")
    from crew_health.plans.application.runs import PlanRuns

    _, run = PlanRuns(s.world).start(
        s.session,
        {"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test"},
        "open_link",
    )
    assert run["link_state_at_open"] == {
        "status": "up",
        "gap_kind": None,
        "next_contact_at": "2026-11-14T11:20:40Z",
    }


@pytest.mark.trace("EI1", "T28-25")
def test_ground_ports_are_named_only_by_the_ground_line_boundary() -> None:
    root = ROOT / "hub/src/crew_health/plans"
    for path in root.rglob("*.py"):
        if path.name == "ground_log.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert not any(name in source for name in ("GroundLinkReader", "InTimeWords", "LinkSnapshot")), path


@pytest.mark.trace("T28-24", "AGENTS-3")
def test_missing_failing_or_incomplete_link_is_unknown_without_hiding_storage_errors() -> None:
    copy = UiCopy(load_json(ROOT / "docs/product/api/examples/config/ui-copy.example.json"))
    reader = Reader()
    for line in (GroundLine(None, None), GroundLine(reader, None)):
        assert line.line(copy, NOW) == ("Unknown", None)
    reader.snapshot = replace(reader.snapshot, next_contact_at=None)
    assert GroundLine(reader, Words()).line(copy, NOW) == ("Unknown", None)
    reader.error = RuntimeError("offline")
    assert GroundLine(reader, Words()).line(copy, NOW) == ("Unknown", None)
    assert GroundLine(reader, Words()).at_open().status == "down"
    reader.error = StorageUnavailable("io_error")
    with pytest.raises(StorageUnavailable):
        GroundLine(reader, Words()).line(copy, NOW)


@pytest.mark.trace("T28-24", "AGENTS-3")
def test_failed_relative_time_rendering_is_unknown_and_missing_copy_is_typed() -> None:
    from crew_health.kernel.errors import ProblemError

    class FailedWords:
        def __init__(self, failure: Exception) -> None:
            self.failure = failure

        def render(self, now: datetime, until: datetime) -> str:
            raise self.failure

    copy = UiCopy(load_json(ROOT / "docs/product/api/examples/config/ui-copy.example.json"))
    assert GroundLine(Reader(), FailedWords(RuntimeError("unavailable"))).line(copy, NOW) == ("Unknown", None)
    with pytest.raises(StorageUnavailable):
        GroundLine(Reader(), FailedWords(StorageUnavailable("io_error"))).line(copy, NOW)
    with pytest.raises(ProblemError) as caught:
        GroundLine(None, None).line(UiCopy({}), NOW)
    assert caught.value.retryable


@pytest.mark.trace("T28-24", "T28-25", "SYSTEM-5.12")
def test_optional_ground_ports_resolve_at_binding_and_reach_http_and_audit(tmp_path: Path) -> None:
    from crew_health.plans.application.ground_log import GroundLinkReader, InTimeWords

    from .http_harness import HttpStack

    h = HttpStack(tmp_path, ((GroundLinkReader, Reader()), (InTimeWords, Words())))
    response = h.client.get("/v1/conditions/ICL10/plan")
    assert response.status_code == 200
    expected = rows(load_json(ROOT / "contracts/api/examples/plans/view-icl10-lunar.json")["stages"])[-1]
    assert rows(response.json()["stages"])[-1]["link_text"] == expected["link_text"]
    run = h.start()
    assert run["link_state_at_open"] == {
        "status": "down",
        "gap_kind": "unknown",
        "next_contact_at": "2026-11-14T11:20:40Z",
    }
