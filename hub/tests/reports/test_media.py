"""T-E09-01 R14 D18 E09-01: media kind from bytes, exact size bound, quarantine, no extension trust."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from .reports_harness import JPEG, MAX_REPORT, PDF, PNG

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.reports.domain.media import check_media, detect_kind, sha256_hex

pytestmark = pytest.mark.trace("T-E09-01", "R14", "V21", "E09-01")

GOOD = {"pdf": PDF, "png": PNG, "jpeg": JPEG}
TYPES = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg"}


def code_of(error: pytest.ExceptionInfo[ProblemError]) -> ErrorCode:
    return error.value.code


@pytest.mark.parametrize("kind", ["pdf", "png", "jpeg"])
def test_supported_kinds_are_accepted_by_their_bytes(kind: str) -> None:
    decision = check_media(GOOD[kind], declared_type=TYPES[kind], max_bytes=MAX_REPORT)
    assert (decision.state, decision.kind, decision.media_type, decision.reason) == (
        "accepted",
        kind,
        TYPES[kind],
        None,
    )


@pytest.mark.parametrize("declared", [None, "", "application/octet-stream", "Application/PDF; charset=x"])
def test_declared_type_that_makes_no_claim_or_agrees_is_accepted(declared: str | None) -> None:
    assert check_media(PDF, declared_type=declared, max_bytes=MAX_REPORT).state == "accepted"


def test_extension_never_decides_the_kind() -> None:
    # The name is not even an input of the policy: a PNG named .pdf is a PNG, a PDF named .png a PDF.
    assert check_media(PNG, declared_type="image/png", max_bytes=MAX_REPORT).kind == "png"
    assert detect_kind(PDF) == "pdf"


@pytest.mark.parametrize("declared", ["image/png", "text/html", "application/zip"])
def test_declared_type_mismatch_is_quarantined(declared: str) -> None:
    decision = check_media(PDF, declared_type=declared, max_bytes=MAX_REPORT)
    assert (decision.state, decision.reason) == ("quarantined", "type_mismatch")


@pytest.mark.parametrize(
    ("kind", "broken"),
    [
        ("pdf", PDF.replace(b"%%EOF\n", b"")),
        ("png", PNG[:-4]),
        ("png", PNG[:12] + b"XXXX" + PNG[16:]),
        ("jpeg", JPEG[:-2]),
    ],
)
def test_truncated_or_corrupt_is_quarantined(kind: str, broken: bytes) -> None:
    decision = check_media(broken, declared_type=TYPES[kind], max_bytes=MAX_REPORT)
    assert (decision.state, decision.reason) == ("quarantined", "truncated")


@pytest.mark.parametrize("marker", [b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction"])
def test_pdf_active_content_is_quarantined(marker: bytes) -> None:
    unsafe = PDF.replace(b"/Catalog", marker)
    decision = check_media(unsafe, declared_type="application/pdf", max_bytes=MAX_REPORT)
    assert (decision.state, decision.reason) == ("quarantined", "active_content")


def test_a_name_that_only_starts_like_a_marker_is_not_active_content() -> None:
    decision = check_media(PDF.replace(b"/Catalog", b"/JSONish"), declared_type=None, max_bytes=MAX_REPORT)
    assert decision.state == "accepted"


@pytest.mark.parametrize("data", [b"GIF89a....", b"PK\x03\x04zip", b"<html>", b"\x00" * 64, b"%PD"])
def test_unsupported_kind_is_refused_not_stored(data: bytes) -> None:
    with pytest.raises(ProblemError) as error:
        check_media(data, declared_type="application/pdf", max_bytes=MAX_REPORT)
    assert code_of(error) is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_size_bound_is_exact_and_injected() -> None:
    assert MAX_REPORT == 20000000  # SYSTEM 5.11 private.max_report_bytes, openapi uploadReport
    pad = b"\x00" * (MAX_REPORT - len(PDF))
    at_limit = PDF[:-6] + pad[: MAX_REPORT - len(PDF)] + PDF[-6:]
    assert len(at_limit) == MAX_REPORT
    assert check_media(at_limit, declared_type=None, max_bytes=MAX_REPORT).state == "accepted"
    with pytest.raises(ProblemError) as error:
        check_media(at_limit + b"\x00", declared_type=None, max_bytes=MAX_REPORT)
    assert code_of(error) is ErrorCode.PAYLOAD_TOO_LARGE


def test_empty_file_and_invalid_bound_are_refused() -> None:
    with pytest.raises(ProblemError) as empty:
        check_media(b"", declared_type=None, max_bytes=MAX_REPORT)
    assert code_of(empty) is ErrorCode.INVALID_VALUE
    for bound in (0, -1):
        with pytest.raises(ProblemError) as invalid:
            check_media(PDF, declared_type=None, max_bytes=bound)
        assert code_of(invalid) is ErrorCode.CONFIG_INVALID


@given(st.binary(max_size=300), st.integers(min_value=1, max_value=400))
def test_policy_is_total_and_never_accepts_an_unsupported_kind(data: bytes, bound: int) -> None:
    try:
        decision = check_media(data, declared_type=None, max_bytes=bound)
    except ProblemError as error:
        assert error.code in {
            ErrorCode.INVALID_VALUE,
            ErrorCode.PAYLOAD_TOO_LARGE,
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        }
        return
    assert detect_kind(data) == decision.kind
    assert len(data) <= bound
    if decision.state == "accepted":
        assert decision.reason is None


@given(st.binary(min_size=1, max_size=64))
def test_hash_is_the_sha256_of_the_exact_bytes(data: bytes) -> None:
    import hashlib

    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()
