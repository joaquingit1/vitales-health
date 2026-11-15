from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.testing import MemoryClock, MemoryIds, MemorySettings
from crew_health.kernel.units import Quantity, Unit
from crew_health.knowledge.application.evidence_query import EvidenceQueryService
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.extraction import Extraction, PageRange
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import ApplicabilityFilter, ReleaseMembership, UseStatus
from crew_health.knowledge.domain.results import (
    Eligible,
    Hits,
    IndexUnavailable,
    InReleaseHistory,
    NoMatch,
    NotApprovedForUse,
    NotInRelease,
    QueryRejected,
    ReleaseUnavailable,
)


class CandidateIndex:
    def __init__(self, result: tuple[str, ...] | IndexUnavailable) -> None:
        self.result = result

    def search(
        self, release_id: str, query: str, use: str, filters: ApplicabilityFilter, mode: str, k: int
    ) -> tuple[str, ...] | IndexUnavailable:
        return self.result


class StepClock(MemoryClock):
    def __init__(self, at: datetime, step: int) -> None:
        super().__init__(at, 0, "epoch_a")
        self.step = step

    def monotonic_ns(self) -> int:
        result = self.monotonic
        self.monotonic += self.step
        return result


def membership(at: datetime) -> ReleaseMembership:
    text = Path("knowledge/inputs/package/NASA-SOURCES.md").read_text(encoding="utf-8").splitlines()[0]
    source = Extraction.create(
        ids=MemoryIds(identifiers=()),
        document_version_id="dv_source",
        format_version="1",
        text=text,
        pages=(PageRange(1, 0, len(text)),),
        extracted_by="editor_a",
        max_chars=len(text),
        max_pages=1,
    )

    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=source,
        char_start=0,
        char_end=len(source.text),
        max_chars=len(source.text),
    )
    return ReleaseMembership(
        "evr_a",
        passage,
        source,
        ("S01",),
        LicenseRecord("lic_a", "a" * 64, "nasa_public_domain", "S01", "editor_a", at),
        (UseStatus("risk_context", "approved", "approval_a", None),),
        "not_clinically_reviewed",
        None,
        "section_a",
        False,
        False,
    )


@pytest.mark.trace("T-E10-10", "T-E10-16", "T-E10-48", "T-E10-49", "R21", "I5")
def test_read_requires_exact_release_and_purpose(at: datetime, settings: MemorySettings) -> None:
    item = membership(at)
    service = EvidenceQueryService((item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    assert isinstance(service.read_approved("evr_a", item.passage.passage_id, "risk_context"), Eligible)
    assert isinstance(
        service.read_approved("evr_a", item.passage.passage_id, "patient_instruction"), NotApprovedForUse
    )
    assert isinstance(service.read_approved("evr_a", "psg_absent", "risk_context"), NotInRelease)
    assert isinstance(
        service.read_approved("evr_b", item.passage.passage_id, "risk_context"), ReleaseUnavailable
    )
    assert isinstance(service.read_approved("evr_a", item.passage.passage_id, "invented"), NotApprovedForUse)


@pytest.mark.trace("T-E10-10", "T-E10-49", "T-E10-52", "R21", "I5")
def test_history_does_not_authorize_current_use(at: datetime, settings: MemorySettings) -> None:
    item = membership(at)
    historical = replace(item, release_id="evr_b", uses=(replace(item.uses[0], status="superseded"),))
    service = EvidenceQueryService((item, historical), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    assert isinstance(
        service.read_approved("evr_b", item.passage.passage_id, "risk_context"), InReleaseHistory
    )
    history = service.inspect_history(item.passage.passage_id, None, 1)
    assert history.items == (item,)
    assert history.next_cursor is not None
    page = service.inspect_history(item.passage.passage_id, history.next_cursor, 1)
    assert page.items == (historical,)
    assert page.next_cursor is None
    assert isinstance(
        service.passages_for_sources("evr_b", ("S01",), "risk_context", ApplicabilityFilter(None, True), 10),
        NoMatch,
    )


@pytest.mark.trace("T-E10-20", "T-E10-48", "I3")
@given(st.booleans(), st.booleans())
def test_filters_cannot_promote_unknown_applicability(allow_unknown: bool, known: bool) -> None:
    filters = ApplicabilityFilter(frozenset({"station_leo"}), allow_unknown)
    environments = frozenset({"station_leo"}) if known else None
    assert filters.admits(environments) is (known or allow_unknown)
    assert not filters.admits(frozenset({"mars_surface"}))


@pytest.mark.trace("T-E10-21", "T-E10-49", "T-E10-55", "I3", "I8")
def test_memberships_refuse_unlicensed_text_duplicate_purposes_and_conflicts(at: datetime) -> None:
    item = membership(at)
    for fields in (
        {"license": replace(item.license, kind="metadata_only")},
        {"uses": (item.uses[0], item.uses[0])},
        {"source_ids": ()},
        {"clinical_review": "unknown"},
        {"extraction": replace(item.extraction, extraction_id="ex_different")},
        {"passage": replace(item.passage, page_start=2, page_end=3)},
    ):
        with pytest.raises(EvidenceInvalid):
            replace(item, **fields)


@pytest.mark.trace("T-E10-48", "T-E10-51", "I2", "I5")
def test_source_aliases_and_context_never_duplicate_or_expose_unapproved_text(
    at: datetime, settings: MemorySettings
) -> None:
    item = replace(membership(at), source_ids=("S01", "EV01"))
    service = EvidenceQueryService((item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    filters = ApplicabilityFilter(None, True)
    hits = service.passages_for_sources("evr_a", ("S01", "EV01"), "risk_context", filters, 10)
    assert isinstance(hits, Hits)
    assert tuple(row.membership for row in hits.items) == (item,)
    expanded = service.expand_context("evr_a", item.passage.passage_id, "risk_context", filters, 0)
    assert isinstance(expanded, Hits)
    assert expanded.items[0].membership == item
    assert isinstance(
        service.expand_context("evr_a", item.passage.passage_id, "patient_instruction", filters, 0), NoMatch
    )
    assert isinstance(
        service.expand_context("evr_b", item.passage.passage_id, "risk_context", filters, 0),
        ReleaseUnavailable,
    )
    assert isinstance(
        service.expand_context("evr_a", item.passage.passage_id, "risk_context", filters, -1), QueryRejected
    )


@pytest.mark.trace("T-E10-44", "T-E10-55", "T-E10-58", "I8")
def test_rejected_queries_and_missing_indexes_are_distinct(at: datetime, settings: MemorySettings) -> None:
    item = membership(at)
    service = EvidenceQueryService((item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    filters = ApplicabilityFilter(None, True)
    assert isinstance(
        service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1), IndexUnavailable
    )
    assert isinstance(service.dense_search("evr_a", "NASA", "risk_context", filters, 1), IndexUnavailable)
    for query, k in (
        ("", 1),
        ("x" * (settings.integer("knowledge.query.max_chars") + 1), 1),
        ("NASA", 0),
        ("NASA", True),
    ):
        assert isinstance(service.dense_search("evr_a", query, "risk_context", filters, k), QueryRejected)
    assert isinstance(
        service.lexical_search("evr_a", "NASA", "risk_context", filters, "invalid", 1), QueryRejected
    )
    assert isinstance(
        service.lexical_search("evr_b", "NASA", "risk_context", filters, "exact", 1), ReleaseUnavailable
    )
    for limit in (0, -1, settings.integer("knowledge.query.limit_max") + 1):
        with pytest.raises(EvidenceInvalid):
            service.inspect_history(item.passage.passage_id, None, limit)
        with pytest.raises(EvidenceInvalid):
            service.passages_for_sources("evr_a", ("S01",), "risk_context", filters, limit)
    with pytest.raises(EvidenceInvalid):
        service.inspect_history(item.passage.passage_id, "invalid", 1)
    with pytest.raises(EvidenceInvalid):
        EvidenceQueryService((item, item), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))


@pytest.mark.trace("T-E10-48", "T-E10-49", "I3")
def test_version_withdrawal_denies_every_purpose(at: datetime, settings: MemorySettings) -> None:
    item = replace(membership(at), withdrawn=True)
    service = EvidenceQueryService((item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    assert isinstance(
        service.read_approved("evr_a", item.passage.passage_id, "risk_context"), InReleaseHistory
    )
    assert isinstance(
        service.passages_for_sources("evr_a", ("S01",), "risk_context", ApplicabilityFilter(None, True), 1),
        NoMatch,
    )


@pytest.mark.trace("T-E10-15", "T-E10-44", "T-E10-48", "T-E10-58", "I3", "I8")
def test_lexical_candidates_are_rechecked_and_return_rank_method_only(
    at: datetime, settings: MemorySettings
) -> None:
    item = membership(at)
    filters = ApplicabilityFilter(None, True)
    for candidate, expected in (
        ((), NoMatch),
        ((item.passage.passage_id,), Hits),
        (IndexUnavailable("evr_a", "lexical", "index_unavailable"), IndexUnavailable),
    ):
        service = EvidenceQueryService(
            (item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"), index=CandidateIndex(candidate)
        )
        result = service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1)
        assert isinstance(result, expected)
        if isinstance(result, Hits):
            assert result.items[0].rank == 1
            assert result.items[0].method == "lexical"
            assert result.items[0].membership == item
    for candidate in (("psg_absent",), (item.passage.passage_id, item.passage.passage_id)):
        service = EvidenceQueryService(
            (item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"), index=CandidateIndex(candidate)
        )
        with pytest.raises(EvidenceInvalid):
            service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1)
    service = EvidenceQueryService(
        (item,),
        settings=settings,
        clock=MemoryClock(at, 0, "epoch_a"),
        index=CandidateIndex((item.passage.passage_id,)),
    )
    with pytest.raises(EvidenceInvalid):
        service.lexical_search("evr_a", "NASA", "patient_instruction", filters, "exact", 1)


@pytest.mark.trace("T-E10-48", "T-E10-49", "R21", "I3", "I5")
def test_context_checks_every_neighbor_without_interpolating_gaps(
    at: datetime, settings: MemorySettings
) -> None:
    item = membership(at)
    text = item.extraction.text
    bounds = (0, len(text) // 3, 2 * len(text) // 3, len(text))
    members = tuple(
        replace(
            item,
            passage=PassageArtifact.create(
                ids=MemoryIds(identifiers=()),
                extraction=item.extraction,
                char_start=start,
                char_end=end,
                max_chars=len(text),
            ),
        )
        for start, end in zip(bounds[:-1], bounds[1:], strict=True)
    )
    denied = replace(members[1], uses=(replace(item.uses[0], status="withdrawn"),))
    service = EvidenceQueryService(
        (members[0], denied, members[2]), settings=settings, clock=MemoryClock(at, 0, "epoch_a")
    )
    result = service.expand_context(
        "evr_a", members[0].passage.passage_id, "risk_context", ApplicabilityFilter(None, True), 2
    )
    assert isinstance(result, Hits)
    assert tuple(row.membership for row in result.items) == (members[0], members[2])
    assert all(row.membership.passage.text != denied.passage.text for row in result.items)
    narrowed = service.passages_for_sources(
        "evr_a", ("S01",), "risk_context", ApplicabilityFilter(None, False), 10
    )
    assert isinstance(narrowed, NoMatch)


@pytest.mark.trace("T-E10-49", "T-E10-55", "I3", "I8")
def test_purpose_state_and_membership_preconditions(at: datetime) -> None:
    item = membership(at)
    for fields in (({"use": "unknown"}), ({"status": "unknown"})):
        with pytest.raises(EvidenceInvalid):
            replace(item.uses[0], **fields)
    assert replace(item.uses[0], successor_ref="psg_next").successor_ref == "psg_next"
    for fields in (
        {"withdrawn": None},
        {"simulation_only": None},
        {"source_ids": ("S01", "S01")},
        {"section": ""},
        {"uses": ()},
    ):
        with pytest.raises(EvidenceInvalid):
            replace(item, **fields)
    assert ApplicabilityFilter(None, False).admits(frozenset({"station_leo"}))


@pytest.mark.trace("T-E10-44", "T-E10-54", "T-E10-56", "I8")
def test_expired_query_result_is_unavailable_and_setting_edit_changes_budget(
    at: datetime, settings: MemorySettings
) -> None:
    item = membership(at)
    timeout = settings.quantity("knowledge.query.timeout_ms")
    step = int(timeout.value * 1000000)
    filters = ApplicabilityFilter(None, True)
    clock = StepClock(at, step)
    service = EvidenceQueryService(
        (item,), settings=settings, clock=clock, index=CandidateIndex((item.passage.passage_id,))
    )
    assert isinstance(
        service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1), IndexUnavailable
    )
    assert isinstance(
        service.passages_for_sources("evr_a", ("S01",), "risk_context", filters, 1), IndexUnavailable
    )
    faster = EvidenceQueryService(
        (item,),
        settings=settings,
        clock=StepClock(at, step // 2),
        index=CandidateIndex((item.passage.passage_id,)),
    )
    assert isinstance(faster.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1), Hits)
    settings.values["knowledge.query.timeout_ms"] = Quantity(Decimal(1), Unit("ms"))
    assert isinstance(
        faster.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1), IndexUnavailable
    )
    assert isinstance(
        service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1), IndexUnavailable
    )
    settings.values["knowledge.query.timeout_ms"] = Quantity(Decimal(1), Unit("s"))
    with pytest.raises(EvidenceInvalid, match="query_timeout"):
        service.lexical_search("evr_a", "NASA", "risk_context", filters, "exact", 1)


@pytest.mark.trace("T-E10-48", "T-E10-51", "T-E10-55", "I2", "I8")
def test_source_limit_stops_at_requested_count_and_conflicting_artifact_rejects(
    at: datetime, settings: MemorySettings
) -> None:
    item = membership(at)
    service = EvidenceQueryService((item,), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
    result = service.passages_for_sources(
        "evr_a", ("S01",), "risk_context", ApplicabilityFilter(None, True), 1
    )
    assert isinstance(result, Hits)
    assert len(result.items) == 1
    assert isinstance(
        service.passages_for_sources("evr_b", ("S01",), "risk_context", ApplicabilityFilter(None, True), 1),
        ReleaseUnavailable,
    )
    shorter = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=item.extraction,
        char_start=0,
        char_end=len(item.extraction.text) - 1,
        max_chars=len(item.extraction.text),
    )
    conflicting = replace(
        item, release_id="evr_b", passage=replace(shorter, passage_id=item.passage.passage_id)
    )
    with pytest.raises(EvidenceInvalid, match="artifact_conflict"):
        EvidenceQueryService((item, conflicting), settings=settings, clock=MemoryClock(at, 0, "epoch_a"))
