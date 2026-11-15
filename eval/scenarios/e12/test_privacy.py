"""E12 privacy scenarios (docs/build/E12/PLAN.md, test_privacy.py) through the production gateway and
E02-backed SessionAuthority: a crewmate's identifier, name or stored observation never reaches a handler or an
owner query, a result naming a crewmate is never published, revocation stops inference, traversal-shaped
targets never reach the retrieval owner, and audits carry only safe reasons."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from crew_health.knowledge.application.dto import AllowedUse, ApprovedRead
from crew_health.tools.application.retrieval_service import PublicQueryBoundary, RetrievalService
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.tool_result import ToolCall
from e12_stack import CREWMATE, SUBJECT, Authored, capability, stack
from tests.tools.test_gateway import View, scope
from tests.tools.test_retrieval_service import Evidence, member, service, settings


def codes(call_id: str, code: str) -> ToolCall:
    return ToolCall(call_id, "get_capabilities", json.dumps({"codes": [code]}).encode())


def search(query: str) -> ToolCall:
    return ToolCall("call_a", "search_evidence", json.dumps({"query": query, "filters": {}}).encode())


def verified(evidence: Evidence) -> RetrievalService:
    boundary = PublicQueryBoundary(
        ("headache", "ben headache"),
        model_release_id="model_a",
        settings=settings(),
        public_scope=lambda query, pin: pin == "model_a",
    )
    return service(evidence, boundary=boundary)


@pytest.mark.trace("R20", "V25", "V26", "T-E12-030")
@pytest.mark.parametrize("query", ["ben headache", CREWMATE + " headache", "ana headache"])
def test_a_query_naming_anyone_on_the_roster_never_reaches_the_evidence_owner(query: str) -> None:
    evidence = Evidence(member("psg_a"))
    view = View()
    result = stack(verified(evidence)).gateway.execute(
        view, replace(scope(), phase=Phase.ASSESSMENT), search(query)
    )
    assert result.status == "denied"
    assert result.data is None
    assert evidence.searched == []
    recorded = str(view.audits).casefold()
    assert "ben" not in recorded
    assert CREWMATE not in recorded
    assert "headache" not in recorded


@pytest.mark.trace("R20", "V25", "T-E12-004")
def test_another_members_stored_observation_is_refused_and_the_own_one_is_admitted() -> None:
    run = stack()
    run.capabilities.items = (
        capability("obs_mine", "available", None),
        capability("obs_theirs", "available", None),
    )
    run.records.rows = {"obs_mine": Authored(SUBJECT, SUBJECT), "obs_theirs": Authored(CREWMATE, SUBJECT)}
    view = View()
    theirs = run.gateway.execute(view, scope(), codes("call_a", "obs_theirs"))
    mine = run.gateway.execute(view, scope(), codes("call_b", "obs_mine"))
    assert (theirs.status, mine.status) == ("denied", "ok")
    assert "obs_theirs" not in str(view.audits)


@pytest.mark.trace("R20", "V25", "I1")
def test_a_result_that_names_a_crewmate_is_never_published() -> None:
    run = stack()
    leaked = capability("pulse", "available", None)
    run.capabilities.items = (replace(leaked, label="Pulse check for Ben"),)
    view = View()
    result = run.gateway.execute(view, scope(), codes("call_a", "pulse"))
    assert result.status != "ok"
    assert result.data is None
    assert "ben" not in str(view.audits).casefold()


@pytest.mark.trace("R20", "V26", "T-E12-004")
def test_revocation_during_inference_stops_the_call_before_the_owner() -> None:
    evidence = Evidence(member("psg_a"))
    run = stack(verified(evidence))
    run.sessions.live = False
    result = run.gateway.execute(View(), replace(scope(), phase=Phase.ASSESSMENT), search("headache"))
    assert result.status == "denied"
    assert evidence.searched == []


@pytest.mark.trace("R23", "V33", "T-E12-030")
@pytest.mark.parametrize(
    "target", ["../journal/psg_a", "/etc/passwd", "psg_a/../../private", "C:\\\\journal"]
)
def test_traversal_shaped_targets_never_reach_the_retrieval_owner(target: str) -> None:
    class Watched(Evidence):
        def read_approved(self, release_id: str, passage_id: str, use: AllowedUse) -> ApprovedRead:
            self.searched.append(passage_id)
            return super().read_approved(release_id, passage_id, use)

    evidence = Watched(member("psg_a"))
    call = ToolCall(
        "call_a", "read_source_passage", json.dumps({"passage_id": target, "include_parent": False}).encode()
    )
    result = stack(service(evidence)).gateway.execute(View(), replace(scope(), phase=Phase.ASSESSMENT), call)
    assert result.status in {"denied", "rejected"}
    assert result.data is None
    assert evidence.searched == []


@pytest.mark.trace("R20", "I7")
def test_without_e02_identity_no_call_is_admitted() -> None:
    run = stack(identity=False)
    run.capabilities.items = (capability("pulse", "available", None),)
    result = run.gateway.execute(View(), scope(), codes("call_a", "pulse"))
    assert result.status == "denied"
