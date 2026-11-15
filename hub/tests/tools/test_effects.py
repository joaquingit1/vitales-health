from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from tests.tools.test_bounds import settings_values
from tests.tools.test_gateway import Authority, View, scope

from crew_health.kernel.testing import MemorySettings
from crew_health.measurement.application.ports import ProposalResult
from crew_health.platform.testing.sqlite import database
from crew_health.tools.application.effects import Effects
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.tool_result import PendingEffect
from crew_health.tools.settings import setting_specs


class Validator:
    def validate(self, uow: object, scope: object, action: object) -> None:
        pass


def settings() -> MemorySettings:
    return MemorySettings(setting_specs(), settings_values())


class Measurements:
    def __init__(self) -> None:
        self.proposals: list[object] = []

    def propose(
        self, uow: object, *, requester: object, subject_person_id: str, proposal: object
    ) -> ProposalResult:
        self.proposals.append(proposal)
        return ProposalResult("mr_a", "unavailable", "KIT_CANNOT_PERFORM", "gap_a")


def proposal() -> PendingEffect:
    document = json.loads(
        Path("contracts/api/examples/assistant/conversation.json").read_text(encoding="utf-8")
    )
    action = next(
        message["action"]
        for message in document["value"]["messages"]
        if message["action"] is not None and message["action"]["action"] == "request_measurement"
    )
    action["state_snapshot_version"] = 1
    return PendingEffect(
        "turn_a",
        "call_a",
        1,
        1,
        json.dumps(action).encode("utf-8"),
        (),
    )


@pytest.mark.trace("T-E12-011", "R45", "D06", "D30", "V78", "I5")
def test_unavailable_request_retains_owner_gap_and_does_not_start_acquisition(tmp_path: Path) -> None:
    owner = Measurements()
    effects = Effects(owner, Authority(), settings=settings(), validator=Validator())
    db = database(tmp_path)
    with db.begin(actor=View.actor, correlation_id="cor_a") as uow:
        result = effects.apply(uow, replace(scope(), config_release_id="release_1"), (proposal(),), "turn_a")
    assert result.request_ids == ("mr_a",)
    assert result.gap_ids == ("gap_a",)
    action = json.loads(proposal().arguments)
    assert owner.proposals[0].reason == action["reason"]
    assert owner.proposals[0].decision_affected == action["decision_affected"]


@pytest.mark.trace("T-E12-013", "T-E12-014", "V30", "I5", "I6")
@pytest.mark.parametrize(
    "change", [{"turn_id": "turn_old"}, {"generation": 2}, {"source_revision": 2}, {"arguments": b"{}"}]
)
def test_rejected_or_stale_effect_has_no_owner_call(change: dict[str, object]) -> None:
    owner = Measurements()
    with pytest.raises(ToolsInvalid):
        Effects(owner, Authority(), settings=settings(), validator=Validator()).apply(
            View(), scope(), (replace(proposal(), **change),), "turn_a"
        )
    assert not owner.proposals


@pytest.mark.trace("T-E12-011", "T-E12-013", "I5")
def test_nullable_action_decision_is_not_filled_from_reason() -> None:
    import json

    owner = Measurements()
    value = json.loads(proposal().arguments)
    value["decision_affected"] = None
    pending = replace(proposal(), arguments=json.dumps(value).encode())
    with pytest.raises(ToolsInvalid):
        Effects(owner, Authority(), settings=settings(), validator=Validator()).apply(
            View(), scope(), (pending,), "turn_a"
        )
    assert not owner.proposals


@pytest.mark.trace("T-E12-011", "T-E12-013", "T-E12-014", "I5", "I6")
@pytest.mark.parametrize(
    "fault", ["no_owner", "no_validator", "empty", "multiple", "over_budget", "bad_scope"]
)
def test_effect_missing_dependencies_and_bounds_fail_without_writing(fault: str) -> None:
    owner = Measurements()
    effects = (
        ()
        if fault == "empty"
        else (proposal(),) * (9 if fault == "over_budget" else 2 if fault == "multiple" else 1)
    )
    engine = Effects(
        None if fault == "no_owner" else owner,
        Authority(),
        settings=settings(),
        validator=None if fault == "no_validator" else Validator(),
    )
    if fault == "empty":
        assert engine.apply(View(), scope(), effects, "turn_a").request_ids == ()
    else:
        with pytest.raises(ToolsInvalid):
            engine.apply(View(), scope(), effects, "different" if fault == "bad_scope" else "turn_a")
    assert not owner.proposals


@pytest.mark.trace("T-E12-011", "T-E12-014", "R45", "I5")
@pytest.mark.parametrize(
    "outcome",
    [
        ProposalResult("mr_a", "proposed", None, None),
        ProposalResult("mr_a", "unavailable", "KIT_CANNOT_PERFORM", None),
        ProposalResult("mr_a", "invalid", None, None),
    ],
)
def test_owner_outcome_cannot_lose_unavailable_episode_gap(tmp_path: Path, outcome: ProposalResult) -> None:
    class Owner(Measurements):
        def propose(
            self, uow: object, *, requester: object, subject_person_id: str, proposal: object
        ) -> ProposalResult:
            return outcome

    db = database(tmp_path)
    target = replace(scope(), config_release_id="release_1", episode_id="episode_a")
    engine = Effects(Owner(), Authority(), settings=settings(), validator=Validator())
    with db.begin(actor=View.actor, correlation_id="cor_a") as uow:
        if outcome.state == "proposed":
            assert engine.apply(uow, target, (proposal(),), "turn_a").measurement_results == (outcome,)
        else:
            with pytest.raises(ToolsInvalid):
                engine.apply(uow, target, (proposal(),), "turn_a")
