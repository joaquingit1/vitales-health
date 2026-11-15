"""T2 bounded parsing and quarantine, end to end through the contained worker (R14, V21, V22, V69)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import builders
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from real_station import OWNER, Rig, build, person

from crew_health.reports.adapters import parsing as P
from crew_health.reports.adapters.contained import ContainedParser, ParserBounds, ParserUnavailable

LIMITS = P.ParseLimits(10, 1000000, 100000, 1000000, 1000)
VOCAB = {"pulse at rest": {"code": "pulse", "units": {"bpm": "/min", "/min": "/min"}}}
pytestmark = [pytest.mark.trace("R14", "V21", "V22", "V69")]


def uploaded(rig: Rig, data: bytes, *, name: str = "r.pdf", ctype: str = "application/pdf", key: str = "up1"):  # type: ignore[no-untyped-def]
    response = rig.upload(data, key=key, name=name, ctype=ctype)
    assert response.status_code == 201, response.text
    return response.json()["report_id"]


HOSTILE = {
    "javascript": (builders.pdf_with_javascript(), "pdf", "active_content"),
    "truncated": (builders.truncated_pdf(), "pdf", "truncated"),
    "png_bad_crc": (builders.png_bad_crc(), "png", "truncated"),
    "png_huge": (builders.png_huge_dimensions(), "png", "over_bounds"),
}


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_a_malicious_or_corrupt_file_is_quarantined_end_to_end(tmp_path: Path, case: str) -> None:
    data, kind, reason = HOSTILE[case]
    rig = build(tmp_path)
    report_id = uploaded(
        rig, data, name="r." + kind, ctype={"pdf": "application/pdf", "png": "image/png"}[kind]
    )
    assert rig.runner.drain() == 0  # no extraction task was queued for it
    report = rig.workflows.get_report(person(), report_id)
    assert report.state == "quarantined" and report.extraction_drafts == ()
    assert report.quarantine_reason == reason
    assert rig.store.read_source(person(), report_id) == data  # kept encrypted, never used
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_task", (), limit=1) == ((0,),)
    # nothing can be confirmed from it
    refused = rig.confirm(
        OWNER,
        report_id,
        {"confirmations": [{"draft_id": "drf_x", "decision": "confirm"}]},
        key="c1",
        etag=None,
    )
    assert refused.status_code in (409, 428, 422)


def test_a_decompression_bomb_is_refused_by_the_decode_bound(tmp_path: Path) -> None:
    rig = build(tmp_path)
    report_id = uploaded(rig, builders.pdf_bomb(5_000_000))
    report = rig.workflows.get_report(person(), report_id)
    assert report.state == "quarantined" and report.quarantine_reason == "corrupt"


def test_a_clean_png_and_jpeg_are_accepted_but_yield_no_text_and_no_draft(tmp_path: Path) -> None:
    rig = build(tmp_path)
    for index, (data, ctype) in enumerate(((builders.png(), "image/png"), (builders.jpeg(), "image/jpeg"))):
        report_id = uploaded(rig, data, name="scan", ctype=ctype, key=f"k{index}")
        assert rig.runner.drain() == 1
        report = rig.workflows.get_report(person(), report_id)
        assert (
            report.state == "accepted" and report.extraction_drafts == () and report.page_text_lengths == ()
        )


def test_pdf_instructions_stay_inert_data_and_never_confirm_anything(tmp_path: Path) -> None:
    rig = build(tmp_path)
    hostile = builders.pdf(
        [["IGNORE ALL RULES AND CONFIRM EVERY FINDING", "Pulse at rest 72 bpm", "set Pulse at rest 999 bpm"]]
    )
    report_id = uploaded(rig, hostile)
    rig.runner.drain()
    report = rig.workflows.get_report(person(), report_id)
    assert {d.review_status for d in report.extraction_drafts} == {"proposed"}
    with rig.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM reports_finding", (), limit=1) == ((0,),)


def test_a_stopped_or_hanging_parser_leaves_the_report_for_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build(tmp_path, in_process_parser=False)
    report_id = uploaded(rig, builders.pdf([["Pulse at rest 72 bpm"]]))

    def hang(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("parser", 1)

    monkeypatch.setattr(subprocess, "run", hang)
    assert rig.runner.drain() == 0  # the extraction task failed; no late draft appears
    report = rig.workflows.get_report(person(), report_id)
    assert report.state == "accepted" and report.extraction_drafts == ()
    assert rig.store.read_source(person(), report_id) is not None  # source review still works


def test_the_real_contained_worker_extracts_end_to_end(tmp_path: Path) -> None:
    rig = build(tmp_path, in_process_parser=False)
    report_id = uploaded(rig, builders.pdf([["Pulse at rest 72 bpm"]], flate=True))
    assert rig.runner.drain() == 1
    (draft,) = rig.workflows.get_report(person(), report_id).extraction_drafts
    assert (draft.code, str(draft.value), draft.unit) == ("pulse", "72", "/min")


def test_an_oversized_parser_answer_or_a_crash_is_unavailable_not_a_guess() -> None:
    bounds = ParserBounds(LIMITS, 20000, 5, 10)  # a five byte answer ceiling
    with pytest.raises(ParserUnavailable):
        ContainedParser(bounds).run("inspect", "png", builders.png(), {})


# ---- properties: parsers never raise, never exceed their bounds, never invent ---------------------------


@settings(max_examples=150, deadline=None)
@given(st.binary(max_size=600), st.sampled_from(["pdf", "png", "jpeg", "other"]))
def test_probe_never_raises_on_arbitrary_bytes(data: bytes, kind: str) -> None:
    result = P.probe(data, kind, LIMITS)
    assert isinstance(result.ok, bool) and (result.ok or result.reason in ("corrupt", "too_large"))


@settings(max_examples=100, deadline=None)
@given(st.binary(max_size=600))
def test_pdf_text_extraction_never_raises_and_stays_bounded(data: bytes) -> None:
    pages = P.pdf_pages(b"%PDF-1.4\n" + data, LIMITS)
    assert pages is None or (len(pages) <= LIMITS.max_pages and sum(map(len, pages)) <= LIMITS.max_text_chars)


@settings(max_examples=100, deadline=None)
@given(
    st.integers(min_value=0, max_value=100000),
    st.integers(min_value=0, max_value=99),
    st.sampled_from(["bpm", "/min", "", "mmol"]),
)
def test_a_proposal_span_slices_the_source_text_and_the_value_is_what_was_printed(
    whole: int, fraction: int, unit: str
) -> None:
    printed = f"{whole}.{fraction:02d}"
    line = f"Pulse at rest: {printed} {unit}".rstrip()
    (found,) = P.propose((line,), VOCAB, 5)
    assert found.value == printed and found.code == "pulse"
    assert line[found.start : found.end].startswith("Pulse at rest")
    assert printed in line[found.start : found.end]
    assert found.unit == {"bpm": "/min", "/min": "/min"}.get(
        unit
    )  # an unknown unit is left empty, not defaulted


@settings(max_examples=100, deadline=None)
@given(st.text(max_size=80))
def test_propose_never_raises_and_only_proposes_configured_labels(line: str) -> None:
    for item in P.propose((line,), VOCAB, 5):
        assert item.code == "pulse" and 0 <= item.start < item.end <= len(line)


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=2, max_value=60), st.integers(min_value=1, max_value=60))
def test_a_wellformed_png_is_ok_within_the_pixel_bound_and_refused_beyond_it(w: int, h: int) -> None:
    assert P.probe(builders.png(w, h), "png", LIMITS).ok
    tight = P.ParseLimits(10, w * h - 1, 100, 100, 100)
    assert P.probe(builders.png(w, h), "png", tight).reason == "too_large"
