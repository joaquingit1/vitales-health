"""RetrievalService over a test double of E10's canonical EvidenceQueryPort (wave 2, 728c2b7).

The double keeps the port's documented behaviour only: read_approved returns the typed outcomes, inspect_history
pages by the last release_id it returned, discrepancies pages with next_cursor. Behaviour the canonical port does
not expose is requested from E10 in docs/requests/E10.md, never assumed here.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from crew_health.guidance.application.ports import GuidanceDecision, Requirements
from crew_health.kernel.testing import MemorySettings
from crew_health.knowledge.application.ports import dto  # type: ignore[attr-defined]
from crew_health.tools.application.retrieval_service import PublicQueryBoundary, RetrievalService
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import RetrievalResult, RetrievalSettings
from crew_health.tools.settings import setting_specs
from tests.tools.test_bounds import settings_values
from tests.tools.test_context_builder import Tokens
from tests.tools.test_gateway import View, scope

PIN = "evr_a"
USE = "risk_context"
NONE = Requirements(frozenset(), {}, frozenset(), "any_crew", None)


def member(
    passage_id: str,
    release_id: str = PIN,
    *,
    text: str = "Rest and fluids; seek review if the headache worsens.",
    status: str = "approved",
) -> dto.ReleaseMembership:
    digest = hashlib.sha256(text.encode()).hexdigest()
    artifact = dto.PassageArtifact(passage_id, "ext_a", 0, len(text), digest, text, "Headache", 3)
    return dto.ReleaseMembership(
        release_id,
        artifact,
        ("src_a",),
        "public_domain",
        "not_clinically_reviewed",
        ((USE, status),),
        (),
        digest,
    )


class Evidence:
    """Canonical EvidenceQueryPort double: rows keyed by (release, passage); history across releases."""

    def __init__(self, *rows: dto.ReleaseMembership) -> None:
        self.rows = {(row.release_id, row.passage.passage_id): row for row in rows}
        self.discrepancy_items: tuple[dto.Discrepancy, ...] = ()
        self.neighbors: tuple[str, ...] = ()
        self.searched: list[str] = []
        self.unavailable = False

    def history_of(self, passage_id: str) -> list[dto.ReleaseMembership]:
        return sorted(
            (row for (_, pid), row in self.rows.items() if pid == passage_id), key=lambda row: row.release_id
        )

    def read_approved(self, release_id: str, passage_id: str, use: str) -> dto.ApprovedRead:
        if self.unavailable:
            return dto.ReleaseUnavailable(release_id, "storage")
        row = self.rows.get((release_id, passage_id))
        if row is None:
            if self.history_of(passage_id):
                return dto.InReleaseHistory(release_id, passage_id)
            return dto.NotInRelease(release_id, passage_id)
        if dict(row.use_status).get(use) != "approved":
            return dto.NotApprovedForUse(release_id, passage_id, use)
        return dto.Eligible(row, release_id, use)

    def inspect_history(self, passage_id: str, cursor: str | None, limit: int) -> dto.HistoryRead:
        rows = [row for row in self.history_of(passage_id) if cursor is None or row.release_id > cursor]
        return tuple(rows[:limit])

    def _hits(self, release_id: str, ids: tuple[str, ...]) -> dto.SearchResult:
        items = tuple(
            dto.RankedPassage(rank, "lexical", self.rows[(release_id, pid)])
            for rank, pid in enumerate(ids, 1)
        )
        return dto.Hits(release_id, items) if items else dto.NoMatch(release_id)

    def lexical_search(
        self, release_id: str, query: str, use: str, filters: object, mode: str, k: int
    ) -> dto.SearchResult:
        self.searched.append(query)
        current = tuple(sorted(pid for rid, pid in self.rows if rid == release_id))
        return self._hits(release_id, current[:k])

    def dense_search(
        self, release_id: str, query: str, use: str, filters: object, k: int
    ) -> dto.SearchResult:
        self.searched.append(query)
        return dto.IndexUnavailable(release_id, "dense", "not_built")

    def expand_context(
        self, release_id: str, passage_id: str, use: str, filters: object, neighbors: int
    ) -> dto.SearchResult:
        return self._hits(release_id, (passage_id, *self.neighbors[: neighbors * 2]))

    def discrepancies(self, release_id: str, cursor: str | None, limit: int) -> dto.DiscrepancyPage:
        start = 0 if cursor is None else int(cursor.removeprefix("page_"))
        items = self.discrepancy_items[start : start + limit]
        more = start + limit < len(self.discrepancy_items)
        return dto.DiscrepancyPage(items, f"page_{start + limit}" if more else None)


class Eligibility:
    outcome = "show"

    def evaluate(self, snapshot: object, request: object) -> GuidanceDecision:
        return GuidanceDecision(
            self.outcome,
            "aboard",
            False,
            (),
            (),
            (),
            "live",
            "not_clinically_reviewed",
            None,  # type: ignore[arg-type]
        )


def settings(**changes: int) -> MemorySettings:
    values = settings_values()
    values.update(changes)
    return MemorySettings(setting_specs(), values)


def service(
    evidence: Evidence,
    *,
    hub: MemorySettings | None = None,
    boundary: PublicQueryBoundary | None = None,
    eligibility: Eligibility | None = None,
) -> RetrievalService:
    return RetrievalService(
        evidence,  # type: ignore[arg-type]
        eligibility or Eligibility(),
        Tokens(),
        hub or settings(),
        RetrievalSettings(3, 3, 3, 8192, 3, Decimal(60), False),
        boundary=boundary,
        dense_unavailable="dense_unavailable",
        no_supported_passage="no_supported_passage",
        context_over_budget="context_over_budget",
        history_incomplete="history_incomplete",
    )


def read(retrieval: RetrievalService, passage_id: str, *, neighbors: int = 0) -> RetrievalResult:
    return retrieval.read(
        View(),
        scope(),
        passage_id,
        use=USE,
        filters=dto.ApplicabilityFilter(),
        requirements=NONE,
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=neighbors,
    )


@pytest.mark.trace("T-E12-030", "T-E12-035", "R21", "V24")
def test_an_eligible_pinned_passage_is_support_with_its_exact_text_and_identity() -> None:
    result = read(service(Evidence(member("psg_a"))), "psg_a")
    assert result.status == "ok"
    [passage] = result.passages
    assert passage.usable_as_support is True
    assert (passage.passage_id, passage.release_id, passage.use_status) == ("psg_a", PIN, "approved")
    assert passage.text == "Rest and fluids; seek review if the headache worsens."
    assert result.history == ()


@pytest.mark.trace("T-E12-035", "R21", "V24")
def test_a_pin_found_only_on_a_later_history_page_is_history_never_support() -> None:
    """Review r1 finding 7: the 51st membership sat on the owner's second history page and E12 gave up. The same
    shape at a page size of 5: the pin's twelfth membership is on the third owner page."""
    rows = [member("psg_old", f"evr_{index:03d}", text="Rest.") for index in range(12)]
    retrieval = service(Evidence(*rows), hub=settings(**{"tools.max_array_items": 5}))
    result = retrieval.read(
        View(),
        replace(scope(), evidence_release_id="evr_z"),
        "psg_old",
        use=USE,
        filters=dto.ApplicabilityFilter(),
        requirements=NONE,
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=0,
    )
    assert result.status == "no_result"
    assert result.passages == ()
    assert len(result.history) == 12
    assert result.history[-1].release_id == "evr_011"
    assert all(item.usable_as_support is False for item in result.history)


@pytest.mark.trace("T-E12-035", "I2", "I7")
def test_history_beyond_the_item_bound_reports_incomplete_not_a_missing_pin() -> None:
    rows = [member("psg_old", f"evr_{index:03d}") for index in range(20)]
    hub = settings(**{"tools.max_array_items": 5, "tools.context.max_items": 10})
    result = service(Evidence(*rows), hub=hub).read(
        View(),
        replace(scope(), evidence_release_id="evr_z"),
        "psg_old",
        use=USE,
        filters=dto.ApplicabilityFilter(),
        requirements=NONE,
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=0,
    )
    assert result.status == "unavailable"
    assert result.limitations == ("history_incomplete",)
    assert result.history_complete is False


@pytest.mark.trace("T-E12-035", "R21", "V24")
def test_contradictory_versions_stay_visible_and_only_the_current_side_supports() -> None:
    current = member("psg_new", text="Give fluids by mouth only.")
    earlier = member("psg_old", "evr_0", text="Give fluids intravenously.")
    evidence = Evidence(current, earlier)
    evidence.discrepancy_items = (
        dto.Discrepancy("dsc_a", "psg_old", "psg_new", "contradicts", ("rev_a",), ()),
    )
    result = read(service(evidence), "psg_new")
    assert result.status == "ok"
    [supporting] = result.passages
    assert (supporting.passage_id, supporting.discrepancy_refs) == ("psg_new", ("dsc_a",))
    assert [(row.passage_id, row.text, row.usable_as_support) for row in result.history] == [
        ("psg_old", "Give fluids intravenously.", False)
    ]


@pytest.mark.trace("T-E12-035", "I7")
def test_discrepancies_on_a_later_owner_page_are_still_found() -> None:
    evidence = Evidence(member("psg_new"), member("psg_old", "evr_0"))
    filler = tuple(dto.Discrepancy(f"dsc_{i}", f"psg_x{i}", f"psg_y{i}", "revises", (), ()) for i in range(7))
    evidence.discrepancy_items = (
        *filler,
        dto.Discrepancy("dsc_last", "psg_old", "psg_new", "revises", (), ()),
    )
    result = read(service(evidence, hub=settings(**{"tools.max_array_items": 5})), "psg_new")
    assert result.passages[0].discrepancy_refs == ("dsc_last",)


@pytest.mark.trace("T-E12-030", "T-E12-034", "I7")
@pytest.mark.parametrize("fault", ["withdrawn", "not_approved", "guidance", "simulation"])
def test_withdrawn_unapproved_or_ineligible_content_never_supports(fault: str) -> None:
    eligibility = Eligibility()
    row = member("psg_a", status="withdrawn" if fault == "withdrawn" else "approved")
    if fault == "not_approved":
        row = replace(row, use_status=(("patient_instruction", "approved"),))
    if fault == "simulation":
        row = replace(row, simulation_only=True)
    if fault == "guidance":
        eligibility.outcome = "not_supported"
    result = read(service(Evidence(row), eligibility=eligibility), "psg_a")
    assert result.status == "no_result"
    assert result.passages == ()


@pytest.mark.trace("T-E12-030", "T-E12-037", "R21", "I7")
def test_an_unapproved_neighbor_is_dropped_and_approved_neighbors_are_reapproved() -> None:
    evidence = Evidence(member("psg_a"), member("psg_b"), member("psg_c", status="withdrawn"))
    evidence.neighbors = ("psg_b", "psg_c")
    result = read(service(evidence), "psg_a", neighbors=1)
    assert [row.passage_id for row in result.passages] == ["psg_a", "psg_b"]


@pytest.mark.trace("T-E12-034", "I5", "I7")
def test_owner_storage_loss_is_unavailable_never_an_empty_answer() -> None:
    evidence = Evidence(member("psg_a"))
    evidence.unavailable = True
    with pytest.raises(ToolsInvalid) as raised:
        read(service(evidence), "psg_a")
    assert raised.value.code.value == "CAPABILITY_UNAVAILABLE"


@pytest.mark.trace("T-E12-031", "T-E12-037", "I7")
def test_a_passage_over_the_result_budget_is_unavailable_not_no_match() -> None:
    result = read(service(Evidence(member("psg_a")), hub=settings(**{"tools.max_result_bytes": 1})), "psg_a")
    assert result.status == "unavailable"
    assert result.limitations == ("context_over_budget",)


@pytest.mark.trace("T-E12-001", "T-E12-065", "R17", "R20", "I2", "I8")
@pytest.mark.parametrize("query", ["https://example.invalid", "../eval/vault", "SELECT * FROM records"])
def test_a_vocabulary_edit_cannot_authorize_a_forbidden_query(query: str) -> None:
    """Review r1 finding 5: each of these validated once they were placed in the configured vocabulary."""
    with pytest.raises(ToolsInvalid):
        PublicQueryBoundary((query,), model_release_id="model_a", settings=settings())


@pytest.mark.trace("T-E12-030", "R20", "I1", "I7")
def test_search_without_a_verified_public_scope_never_reaches_the_owner() -> None:
    evidence = Evidence(member("psg_a"))
    unverified = PublicQueryBoundary(("headache",), model_release_id="model_a", settings=settings())
    for boundary in (None, unverified):
        with pytest.raises(ToolsInvalid) as raised:
            service(evidence, boundary=boundary).search(
                View(),
                scope(),
                "headache",
                use=USE,
                filters=dto.ApplicabilityFilter(),
                requirements=NONE,
                patient_location_id=None,
                proposed_operator_id=None,
            )
        assert raised.value.code.value == "CAPABILITY_UNAVAILABLE"
    assert evidence.searched == []


@pytest.mark.trace("T-E12-030", "T-E12-033", "R21", "I7")
def test_a_verified_public_query_searches_the_pin_and_reports_lost_dense_ranking() -> None:
    evidence = Evidence(member("psg_a"))
    boundary = PublicQueryBoundary(
        ("headache",),
        model_release_id="model_a",
        settings=settings(),
        public_scope=lambda query, pin: query == "headache" and pin == "model_a",
    )
    result = service(evidence, boundary=boundary).search(
        View(),
        scope(),
        "headache",
        use=USE,
        filters=dto.ApplicabilityFilter(),
        requirements=NONE,
        patient_location_id=None,
        proposed_operator_id=None,
    )
    assert result.status == "ok"
    assert [row.passage_id for row in result.passages] == ["psg_a"]
    assert result.limitations == ("dense_unavailable",)
    assert set(evidence.searched) == {"headache"}
