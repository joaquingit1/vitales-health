from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from tests.knowledge.test_queries import CandidateIndex, membership
from tests.tools.test_bounds import settings_values
from tests.tools.test_context_builder import Tokens
from tests.tools.test_gateway import View, scope

from crew_health.guidance.application.ports import GuidanceDecision, Requirements
from crew_health.kernel.testing import MemoryClock, MemorySettings
from crew_health.knowledge.application.evidence_query import EvidenceQueryService
from crew_health.knowledge.application.ports import ApplicabilityFilter
from crew_health.tools.application.retrieval_service import PublicQueryBoundary, RetrievalService
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import RetrievalSettings
from crew_health.tools.settings import setting_specs


class Eligibility:
    allowed = True

    def evaluate(self, snapshot: object, request: object) -> GuidanceDecision:
        return GuidanceDecision(
            "show" if self.allowed else "not_supported",
            "aboard",
            False,
            (),
            (),
            (),
            "live",
            "not_clinically_reviewed",
            None,
        )


def service(knowledge_settings: MemorySettings) -> tuple[RetrievalService, str, Eligibility]:
    item = replace(membership(View.now), environments=frozenset({"station"}))
    provider = EvidenceQueryService(
        (item,),
        settings=knowledge_settings,
        clock=MemoryClock(View.now, 0, "epoch_a"),
        index=CandidateIndex((item.passage.passage_id,)),
    )
    settings = MemorySettings(setting_specs(), settings_values())
    eligibility = Eligibility()
    boundary = PublicQueryBoundary(
        ("headache",),
        model_release_id="model_a",
        settings=settings,
        public_scope=lambda query, pin: query == "headache" and pin == "model_a",
    )
    return (
        RetrievalService(
            provider,
            eligibility,
            Tokens(),
            settings,
            RetrievalSettings(3, 3, 3, 8192, 3, Decimal(60), False),
            boundary=boundary,
            dense_unavailable="configured dense notice",
            no_supported_passage="configured no support notice",
            context_over_budget="configured budget notice",
        ),
        item.passage.passage_id,
        eligibility,
    )


def search(retrieval: RetrievalService, query: str = "headache"):
    return retrieval.search(
        View(),
        scope(),
        query,
        use="risk_context",
        filters=ApplicabilityFilter(frozenset({"station"}), False),
        requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
        patient_location_id=None,
        proposed_operator_id=None,
    )


@pytest.fixture
def knowledge_settings() -> MemorySettings:
    import json
    from pathlib import Path

    from crew_health.kernel.units import Quantity, Unit
    from crew_health.knowledge.module import setting_specs

    fixture = json.loads(Path("fixtures/knowledge/manifest.json").read_text(encoding="utf-8"))
    values = {
        key: Quantity(Decimal(row["value"]), Unit(row["unit"])) if row["unit"] else row["value"]
        for key, row in fixture["settings"].items()
    }
    return MemorySettings(setting_specs(), values)


@pytest.mark.trace("T-E12-030", "T-E12-033", "R21", "I7")
def test_dense_loss_uses_pinned_eligible_lexical_results(knowledge_settings: MemorySettings) -> None:
    retrieval, passage_id, _ = service(knowledge_settings)
    result = search(retrieval)
    assert result.status == "ok"
    assert [item.passage_id for item in result.passages] == [passage_id]
    assert result.passages[0].release_id == "evr_a"
    assert result.passages[0].clinical_review == "not_clinically_reviewed"
    assert result.limitations == ("configured dense notice",)


@pytest.mark.trace("T-E12-031", "T-E12-034", "V28", "V34")
def test_no_eligible_support_is_no_result_not_invented_data(knowledge_settings: MemorySettings) -> None:
    retrieval, _, eligibility = service(knowledge_settings)
    eligibility.allowed = False
    result = search(retrieval)
    assert result.status == "no_result"
    assert not result.passages
    assert result.limitations[-1] == "configured no support notice"


@pytest.mark.trace("T-E12-001", "T-E12-040", "R17", "R20", "V25", "V26")
@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM records",
        "https://example.invalid",
        "../eval/vault",
        "person_a headache",
        "private journal text",
        "headache OR other",
    ],
)
def test_private_or_executable_query_is_never_forwarded(
    knowledge_settings: MemorySettings, query: str
) -> None:
    retrieval, _, _ = service(knowledge_settings)
    with pytest.raises(ToolsInvalid, match="MODEL_OUTPUT_REJECTED"):
        search(retrieval, query)


@pytest.mark.trace("T-E12-031", "T-E12-037", "I7")
def test_too_small_budget_is_unavailable_not_no_match(knowledge_settings: MemorySettings) -> None:
    retrieval, _, _ = service(knowledge_settings)
    retrieval._retrieval = replace(retrieval._retrieval, budget_tokens=1)
    result = search(retrieval)
    assert result.status == "unavailable"


def read(retrieval: RetrievalService, passage_id: str, *, neighbors: int = 0):
    return retrieval.read(
        View(),
        scope(),
        passage_id,
        use="risk_context",
        filters=ApplicabilityFilter(frozenset({"station"}), False),
        requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=neighbors,
    )


@pytest.mark.trace("T-E12-030", "T-E12-035", "T-E12-037", "R21", "V24")
def test_exact_citation_and_neighbor_expansion_preserve_intact_text(
    knowledge_settings: MemorySettings,
) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    direct = read(retrieval, identifier)
    expanded = read(retrieval, identifier, neighbors=1)
    assert direct == expanded
    assert direct.status == "ok" and len(direct.passages) == 1
    assert direct.passages[0].content_sha256
    assert read(retrieval, "psg_absent").status == "no_result"


@pytest.mark.trace("T-E12-031", "T-E12-034", "I7")
@pytest.mark.parametrize("fault", ["no_result", "index", "release", "pin", "duplicates", "rank"])
def test_search_preserves_typed_failures_and_rejects_provider_corruption(
    knowledge_settings: MemorySettings, monkeypatch, fault: str
) -> None:
    from crew_health.knowledge.domain.results import (
        Hits,
        IndexUnavailable,
        NoMatch,
        RankedItem,
        ReleaseUnavailable,
    )

    retrieval, _, _ = service(knowledge_settings)
    item = replace(membership(View.now), environments=frozenset({"station"}))
    results = {
        "no_result": NoMatch("evr_a"),
        "index": IndexUnavailable("evr_a", "lexical", "unavailable"),
        "release": ReleaseUnavailable("evr_a", "unavailable"),
        "pin": NoMatch("evr_b"),
        "duplicates": Hits("evr_a", (RankedItem(item, 1, "lexical"), RankedItem(item, 2, "lexical"))),
        "rank": Hits("evr_a", (RankedItem(item, 2, "lexical"),)),
    }
    monkeypatch.setattr(retrieval._reader, "lexical_search", lambda *args: results[fault])
    if fault == "no_result":
        assert search(retrieval).status == "no_result"
    else:
        with pytest.raises(ToolsInvalid):
            search(retrieval)


@pytest.mark.trace("T-E12-035", "R21", "V24")
def test_superseded_passage_is_separate_non_supporting_history(knowledge_settings: MemorySettings) -> None:
    retrieval, _, _ = service(knowledge_settings)
    item = membership(View.now)
    old = replace(item, uses=(replace(item.uses[0], status="superseded"),))
    retrieval._reader = EvidenceQueryService(
        (old,), settings=knowledge_settings, clock=MemoryClock(View.now, 0, "epoch_a")
    )
    result = read(retrieval, item.passage.passage_id)
    assert result.status == "no_result" and not result.passages
    assert result.history[0].release_id == "evr_a"
    assert result.history[0].text == item.passage.text


@pytest.mark.trace("T-E12-030", "T-E12-034", "I7")
@pytest.mark.parametrize("fault", ["tokenizer", "boundary", "reranker", "scope", "neighbors", "filter"])
def test_missing_pins_artifacts_and_invalid_filters_cannot_enable_retrieval(
    knowledge_settings: MemorySettings, fault: str
) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    if fault == "tokenizer":
        retrieval._tokens = None
    elif fault == "boundary":
        retrieval._boundary = None
    elif fault == "reranker":
        retrieval._retrieval = replace(retrieval._retrieval, rerank=True)
    if fault == "scope":
        with pytest.raises(ToolsInvalid):
            retrieval._boundary.validate("headache", model_release_id="other_model")
    elif fault == "neighbors":
        with pytest.raises(ToolsInvalid):
            read(retrieval, identifier, neighbors=4)
    elif fault == "filter":
        with pytest.raises(ToolsInvalid):
            retrieval.search(
                View(),
                scope(),
                "headache",
                use="risk_context",
                filters=ApplicabilityFilter(frozenset(str(i) for i in range(51)), False),
                requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
                patient_location_id=None,
                proposed_operator_id=None,
            )
    else:
        with pytest.raises(ToolsInvalid):
            search(retrieval)


@pytest.mark.trace("T-E12-034", "I5", "I7")
def test_storage_error_is_not_an_empty_retrieval(knowledge_settings: MemorySettings, monkeypatch) -> None:
    from crew_health.kernel.errors import StorageUnavailable

    retrieval, _, _ = service(knowledge_settings)

    def broken(*args):
        raise StorageUnavailable("corrupt")

    monkeypatch.setattr(retrieval._reader, "lexical_search", broken)
    with pytest.raises(StorageUnavailable):
        search(retrieval)


@pytest.mark.trace("T-E12-005", "T-E12-067", "I2")
@pytest.mark.parametrize("queries", [(), ("x" * 301,), tuple(str(i) for i in range(51))])
def test_public_vocabulary_is_bounded_at_startup(queries: tuple[str, ...]) -> None:
    with pytest.raises(ToolsInvalid):
        PublicQueryBoundary(
            queries, model_release_id="model_a", settings=MemorySettings(setting_specs(), settings_values())
        )


@pytest.mark.trace("T-E12-030", "T-E12-034", "I7")
@pytest.mark.parametrize("fault", ["withdrawn", "simulation", "wrong_pin", "wrong_review", "not_approved"])
def test_citation_rechecks_current_eligibility_after_search(
    knowledge_settings: MemorySettings, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from crew_health.knowledge.domain.results import Eligible, Hits, NotApprovedForUse, RankedItem

    retrieval, _, _ = service(knowledge_settings)
    item = replace(membership(View.now), environments=frozenset({"station"}))
    monkeypatch.setattr(
        retrieval._reader, "lexical_search", lambda *args: Hits("evr_a", (RankedItem(item, 1, "lexical"),))
    )
    if fault == "withdrawn":
        item = replace(item, withdrawn=True)
    elif fault == "simulation":
        item = replace(item, simulation_only=True)
    elif fault == "wrong_pin":
        item = replace(item, release_id="evr_b")
    elif fault == "wrong_review":
        item = replace(item, clinical_review="clinically_reviewed")
    result = (
        NotApprovedForUse("evr_a", item.passage.passage_id, "risk_context")
        if fault == "not_approved"
        else Eligible(item, "risk_context")
    )
    monkeypatch.setattr(retrieval._reader, "read_approved", lambda *args: result)
    if fault == "simulation":
        assert search(retrieval).status == "no_result"
    else:
        with pytest.raises(ToolsInvalid):
            search(retrieval)


@pytest.mark.trace("T-E12-030", "T-E12-037", "R21", "I7")
def test_each_expanded_neighbor_is_reapproved_and_intact(knowledge_settings: MemorySettings) -> None:
    from crew_health.kernel.testing import MemoryIds
    from crew_health.knowledge.domain.passage import PassageArtifact

    retrieval, _, _ = service(knowledge_settings)
    original = replace(membership(View.now), environments=frozenset({"station"}))
    passage = PassageArtifact.create(
        ids=MemoryIds(identifiers=()),
        extraction=original.extraction,
        char_start=len(original.extraction.text) - 1,
        char_end=len(original.extraction.text),
        max_chars=knowledge_settings.integer("knowledge.passage.max_chars"),
    )
    neighbor = replace(original, passage=passage)
    retrieval._reader = EvidenceQueryService(
        (original, neighbor),
        settings=knowledge_settings,
        clock=MemoryClock(View.now, 0, "epoch_a"),
        index=CandidateIndex((original.passage.passage_id, neighbor.passage.passage_id)),
    )
    result = read(retrieval, original.passage.passage_id, neighbors=1)
    assert len(result.passages) == 2
    assert result.passages[1].text == neighbor.passage.text
    retrieval._retrieval = replace(retrieval._retrieval, max_per_parent=1)
    assert len(search(retrieval).passages) == 1
    retrieval._retrieval = replace(retrieval._retrieval, final_max=1, max_per_parent=1)
    assert len(read(retrieval, original.passage.passage_id, neighbors=1).passages) == 1
    assert len(search(retrieval).passages) == 1


@pytest.mark.trace("T-E12-031", "T-E12-034", "T-E12-037", "I7")
def test_citation_pin_and_budget_failures_remain_explicit(knowledge_settings: MemorySettings) -> None:
    retrieval, identifier, eligibility = service(knowledge_settings)
    with pytest.raises(ToolsInvalid):
        retrieval.read(
            View(),
            replace(scope(), evidence_release_id="evr_missing"),
            identifier,
            use="risk_context",
            filters=ApplicabilityFilter(None, False),
            requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
            patient_location_id=None,
            proposed_operator_id=None,
            neighbors=0,
        )
    eligibility.allowed = False
    assert read(retrieval, identifier).status == "no_result"
    eligibility.allowed = True
    retrieval._retrieval = replace(retrieval._retrieval, budget_tokens=1)
    assert read(retrieval, identifier).status == "unavailable"


@pytest.mark.trace("T-E12-032", "T-E12-033", "E12-02")
def test_valid_dense_ranks_fuse_with_lexical_without_replacing_pin(
    knowledge_settings: MemorySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crew_health.knowledge.domain.results import Hits, RankedItem

    retrieval, identifier, _ = service(knowledge_settings)
    item = replace(membership(View.now), environments=frozenset({"station"}))
    monkeypatch.setattr(
        retrieval._reader, "dense_search", lambda *args: Hits("evr_a", (RankedItem(item, 1, "dense"),))
    )
    result = search(retrieval)
    assert result.passages[0].passage_id == identifier
    assert result.limitations == ()


@pytest.mark.trace("T-E12-005", "T-E12-031", "T-E12-034", "I7")
@pytest.mark.parametrize(
    "fault", ["zero_budget", "wrong_config_pin", "no_dense", "no_read_tokenizer", "bad_count", "bytes"]
)
def test_retrieval_resource_and_pin_boundaries(knowledge_settings: MemorySettings, fault: str) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    if fault == "no_dense":
        retrieval._retrieval = replace(retrieval._retrieval, k_dense=0)
        assert search(retrieval).limitations == ()
    elif fault == "zero_budget":
        with pytest.raises(ToolsInvalid):
            RetrievalService(
                retrieval._reader,
                retrieval._eligibility,
                Tokens(),
                retrieval._settings,
                replace(retrieval._retrieval, budget_tokens=0),
                boundary=retrieval._boundary,
                dense_unavailable="configured dense notice",
                no_supported_passage="configured no support notice",
                context_over_budget="configured budget notice",
            )
    elif fault == "wrong_config_pin":
        with pytest.raises(ToolsInvalid):
            retrieval.search(
                View(),
                replace(scope(), config_release_id="cfg_other"),
                "headache",
                use="risk_context",
                filters=ApplicabilityFilter(None, False),
                requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
                patient_location_id=None,
                proposed_operator_id=None,
            )
    elif fault == "no_read_tokenizer":
        retrieval._tokens = None
        with pytest.raises(ToolsInvalid):
            read(retrieval, identifier)
    elif fault == "bad_count":

        class Broken(Tokens):
            def count(self, prompt: bytes, *, model_release_id: str) -> int:
                return -1

        retrieval._tokens = Broken()
        with pytest.raises(ToolsInvalid):
            search(retrieval)
    elif fault == "bytes":
        values = settings_values()
        from crew_health.kernel.units import Quantity, Unit

        values["tools.max_result_bytes"] = Quantity(Decimal(1), Unit("By"))
        retrieval._settings = MemorySettings(setting_specs(), values)
        assert search(retrieval).status == "unavailable"


@pytest.mark.trace("T-E12-034", "T-E12-035", "T-E12-037", "R21", "I7")
@pytest.mark.parametrize(
    "fault", ["history_overflow", "history_missing_pin", "history_budget", "ranking_overflow"]
)
def test_history_and_ranking_respect_all_bounds(
    knowledge_settings: MemorySettings, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from crew_health.knowledge.domain.results import HistoryRead, Hits, InReleaseHistory, RankedItem

    retrieval, identifier, _ = service(knowledge_settings)
    item = membership(View.now)
    if fault == "ranking_overflow":
        monkeypatch.setattr(
            retrieval._reader,
            "lexical_search",
            lambda *args: Hits("evr_a", tuple(RankedItem(item, n, "lexical") for n in range(1, 5))),
        )
        with pytest.raises(ToolsInvalid):
            search(retrieval)
        return
    monkeypatch.setattr(
        retrieval._reader,
        "read_approved",
        lambda *args: InReleaseHistory("evr_a", identifier, "risk_context"),
    )
    rows = (
        (item,) * 51
        if fault == "history_overflow"
        else (replace(item, release_id="evr_b"),)
        if fault == "history_missing_pin"
        else (item,)
    )
    monkeypatch.setattr(retrieval._reader, "inspect_history", lambda *args: HistoryRead(rows, None))
    if fault == "history_budget":
        retrieval._retrieval = replace(retrieval._retrieval, budget_tokens=1)
        assert read(retrieval, identifier).status == "unavailable"
    else:
        with pytest.raises(ToolsInvalid):
            read(retrieval, identifier)


@pytest.mark.trace("T-E12-001", "T-E12-065", "R17", "I2", "I8")
@pytest.mark.parametrize("query", ["https://example.invalid", "../eval/vault", "SELECT * FROM records"])
def test_vocabulary_edit_cannot_authorize_forbidden_query(query: str) -> None:
    with pytest.raises(ToolsInvalid):
        PublicQueryBoundary(
            (query,), model_release_id="model_a", settings=MemorySettings(setting_specs(), settings_values())
        )


@pytest.mark.trace("T-E12-035", "R21", "V24")
def test_explicit_history_pin_is_found_after_first_owner_page(knowledge_settings: MemorySettings) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    item = membership(View.now)
    members = tuple(
        replace(
            item,
            release_id=f"evr_{n:02}",
            uses=(replace(item.uses[0], status="superseded", successor_ref="psg_successor"),),
        )
        for n in range(51)
    )
    retrieval._reader = EvidenceQueryService(
        members, settings=knowledge_settings, clock=MemoryClock(View.now, 0, "epoch_a")
    )
    result = retrieval.read(
        View(),
        replace(scope(), evidence_release_id="evr_50"),
        identifier,
        use="risk_context",
        filters=ApplicabilityFilter(None, True),
        requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=0,
    )
    assert result.status == "no_result"
    assert result.history[0].release_id == "evr_50"
    assert result.history[0].use_status == "superseded"
    assert result.history[0].successor_ref == "psg_successor"
    assert not result.history[0].usable_as_support


@pytest.mark.trace("T-E12-035", "R21", "V24")
def test_successful_search_keeps_non_supporting_release_history(knowledge_settings: MemorySettings) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    current = replace(membership(View.now), environments=frozenset({"station"}))
    old = replace(
        current,
        release_id="evr_old",
        uses=(replace(current.uses[0], status="superseded", successor_ref=identifier),),
    )
    retrieval._reader = EvidenceQueryService(
        (old, current),
        settings=knowledge_settings,
        clock=MemoryClock(View.now, 0, "epoch_a"),
        index=CandidateIndex((identifier,)),
    )
    result = search(retrieval)
    assert result.status == "ok"
    assert result.passages[0].usable_as_support
    assert result.passages[0].release_id == "evr_a"
    assert len(result.history) == 1
    assert result.history[0].release_id == "evr_old"
    assert result.history[0].use_status == "superseded"
    assert result.history[0].successor_ref == identifier
    assert not result.history[0].usable_as_support


@pytest.mark.trace("T-E12-035", "I2", "I7")
def test_history_bound_reports_incomplete_instead_of_missing_pin(knowledge_settings: MemorySettings) -> None:
    retrieval, identifier, _ = service(knowledge_settings)
    original = membership(View.now)
    rows = tuple(
        replace(original, release_id=f"evr_{i:03}", uses=(replace(original.uses[0], status="superseded"),))
        for i in range(201)
    )
    retrieval._reader = EvidenceQueryService(
        rows, settings=knowledge_settings, clock=MemoryClock(View.now, 0, "epoch_a")
    )
    target = replace(scope(), evidence_release_id="evr_200")
    result = retrieval.read(
        View(),
        target,
        identifier,
        use="risk_context",
        filters=ApplicabilityFilter(None, False),
        requirements=Requirements(frozenset(), {}, frozenset(), "any_crew", None),
        patient_location_id=None,
        proposed_operator_id=None,
        neighbors=0,
    )
    assert result.status == "unavailable"
    assert not result.history_complete
    assert not result.passages
    assert result.limitations == ("configured budget notice",)


@pytest.mark.trace("T-E12-030", "R20", "I1", "I7")
def test_unverified_public_vocabulary_never_reaches_search(
    knowledge_settings: MemorySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    retrieval, _, _ = service(knowledge_settings)
    retrieval._boundary = PublicQueryBoundary(
        ("headache",), model_release_id="model_a", settings=retrieval._settings
    )
    calls: list[str] = []

    def forbidden(*args):
        calls.append("search")
        raise AssertionError("unverified query reached provider")

    monkeypatch.setattr(retrieval._reader, "lexical_search", forbidden)
    monkeypatch.setattr(retrieval._reader, "dense_search", forbidden)
    with pytest.raises(ToolsInvalid, match="CAPABILITY_UNAVAILABLE"):
        search(retrieval)
    assert calls == []


@pytest.mark.trace("T-E12-034", "T-E12-035", "R21", "I7")
@pytest.mark.parametrize("status", ["superseded", "withdrawn", "wrong_use"])
def test_eligible_wrapper_cannot_promote_an_ineligible_membership(
    knowledge_settings: MemorySettings, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from crew_health.knowledge.domain.results import Eligible

    retrieval, identifier, _ = service(knowledge_settings)
    member = replace(membership(View.now), environments=frozenset({"station"}))
    if status == "wrong_use":
        member = replace(member, uses=(replace(member.uses[0], use="patient_instruction"),))
    else:
        member = replace(member, uses=(replace(member.uses[0], status=status),))
    monkeypatch.setattr(retrieval._reader, "read_approved", lambda *args: Eligible(member, "risk_context"))
    with pytest.raises(ToolsInvalid, match="MODEL_OUTPUT_REJECTED"):
        read(retrieval, identifier)
