"""CONTRACTS 21 kit map rules, exactly: state precedence, missing set, regions, what-if diff, kit gate, capabilities.

Pure functions over a parsed KitMapDoc and a kit (the instrument IDs aboard). Nothing here reads observations,
devices, the model or the ground link: kit membership is configuration (H27-4).
"""

from __future__ import annotations

from collections.abc import Sequence

from crew_health.kitmap.domain.model import (
    STATE_ORDER,
    ConditionEvaluation,
    ConditionRow,
    ConditionState,
    GateResult,
    KitCapability,
    KitMapDoc,
    RegionSummary,
    StateCounts,
    TestEvaluation,
    UnknownCapabilityCode,
    UnknownConditionId,
)

WHOLE_BODY = "whole_body"


def evaluate_condition(row: ConditionRow, kit: frozenset[str]) -> ConditionEvaluation:
    tests = tuple(
        TestEvaluation(
            index=test.index,
            instruments=test.instruments,
            needs_no_instrument=not test.instruments,
            fully_aboard=bool(test.instruments) and all(i in kit for i in test.instruments),
            missing=tuple(i for i in test.instruments if i not in kit),
        )
        for test in row.tests
    )
    with_instruments = [t for t in tests if not t.needs_no_instrument]
    if any(t.fully_aboard for t in with_instruments):
        return ConditionEvaluation(row, ConditionState.MEASURED_HERE, tests, ())
    state = (
        ConditionState.REPORT_ONLY
        if any(t.needs_no_instrument for t in tests)
        else ConditionState.NEEDS_TEST_NOT_ABOARD
    )
    missing: tuple[str, ...] = ()
    if with_instruments:
        # Smallest missing set, ties to the first test in document order (min is stable).
        missing = min(with_instruments, key=lambda t: len(t.missing)).missing
    return ConditionEvaluation(row, state, tests, missing)


def evaluate_kit(doc: KitMapDoc, kit: frozenset[str]) -> tuple[ConditionEvaluation, ...]:
    return tuple(evaluate_condition(row, kit) for row in doc.conditions)


def count_states(evals: Sequence[ConditionEvaluation]) -> StateCounts:
    states = [e.state for e in evals]
    return StateCounts(
        measured_here=states.count(ConditionState.MEASURED_HERE),
        report_only=states.count(ConditionState.REPORT_ONLY),
        needs_test_not_aboard=states.count(ConditionState.NEEDS_TEST_NOT_ABOARD),
        total=len(states),
    )


def summarize_regions(
    evals: Sequence[ConditionEvaluation], order: Sequence[str]
) -> tuple[RegionSummary, ...]:
    """One summary per region in the fixed order, then whole_body for rows with []; empty regions omitted."""
    summaries: list[RegionSummary] = []
    for region in (*order, WHOLE_BODY):
        members = [
            e
            for e in evals
            if (region in e.row.body_regions if region != WHOLE_BODY else not e.row.body_regions)
        ]
        if not members:
            continue
        present = {e.state for e in members}
        marks = tuple(s for s in STATE_ORDER if s in present)
        summaries.append(RegionSummary(region, count_states(members), marks))
    return tuple(summaries)


def diff_states(
    base: Sequence[ConditionEvaluation], after: Sequence[ConditionEvaluation]
) -> tuple[tuple[ConditionEvaluation, ConditionEvaluation], ...]:
    """Rows whose state moved, in row order, as (before, after) pairs."""
    if [e.row.condition_id for e in base] != [e.row.condition_id for e in after]:
        raise ValueError("what-if compared two different row sets")
    return tuple((b, a) for b, a in zip(base, after, strict=True) if b.state != a.state)


def _providers(doc: KitMapDoc, capability_code: str) -> tuple[str, ...]:
    return tuple(i.id for i in doc.instruments if capability_code in i.capability_codes)


def kit_gate(
    doc: KitMapDoc, capability_code: str, environment_id: str, condition_ids: Sequence[str]
) -> GateResult:
    if not any(c.code == capability_code for c in doc.capabilities):
        raise UnknownCapabilityCode(capability_code)
    rows = {row.condition_id: row for row in doc.conditions}
    urls: list[str] = []
    for index, condition_id in enumerate(condition_ids):
        row = rows.get(condition_id)
        if row is None:
            raise UnknownConditionId(index, condition_id)
        if row.cliff_url not in urls:
            urls.append(row.cliff_url)
    kit = doc.kits.get(environment_id, frozenset())
    providers = _providers(doc, capability_code)
    aboard = tuple(p for p in providers if p in kit)
    can = bool(aboard)
    missing = () if can else tuple(p for p in providers if p not in kit)
    by_id = {i.id: i for i in doc.instruments}
    words = tuple(by_id[p].name for p in aboard) if can else tuple(by_id[p].needs_text for p in missing)
    return GateResult(
        capability_code=capability_code,
        environment_id=environment_id,
        can=can,
        instrument_ids=aboard,
        missing_instrument_ids=missing,
        condition_ids=tuple(dict.fromkeys(condition_ids)),
        cliff_urls=tuple(urls),
        template_key="can" if can else "cannot",
        instrument_words=words,
        kit_map_version=doc.version,
    )


def derive_capabilities(
    doc: KitMapDoc, environment_id: str, environment_order: Sequence[str]
) -> tuple[KitCapability, ...]:
    kit = doc.kits.get(environment_id, frozenset())
    result: list[KitCapability] = []
    for words in doc.capabilities:
        providers = _providers(doc, words.code)
        aboard = tuple(p for p in providers if p in kit)
        methods = tuple(dict.fromkeys(i.method for i in doc.instruments if i.id in providers))
        result.append(
            KitCapability(
                code=words.code,
                label=words.label,
                body_region=words.body_region,
                operator_requirement=words.operator_requirement,
                expected_duration_seconds=words.expected_duration_seconds,
                methods=methods,
                instrument_ids=aboard,
                missing_instrument_ids=() if aboard else providers,
                applicable_environments=tuple(
                    e for e in environment_order if any(p in doc.kits.get(e, frozenset()) for p in providers)
                ),
                kit_status="available" if aboard else "unavailable",
                kit_reason=None if aboard else "not_in_kit",
            )
        )
    return tuple(result)
