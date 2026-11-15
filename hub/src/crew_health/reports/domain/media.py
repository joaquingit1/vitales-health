"""E09-01 media acceptance: the kind is decided from the bytes, never the extension (R14, V21).

PDF, PNG and JPEG only. The declared type must agree with the checked kind. Unsafe, truncated or mismatched
input is quarantined: stored encrypted by the application, never parsed, executed or used clinically. The
size bound is the hub setting private.max_report_bytes (SYSTEM 5.11), injected, never written here. Full
decode integrity belongs to the contained parser adapter (T2, P6); this is the cheap structural gate."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Literal

from crew_health.kernel.errors import ErrorCode
from crew_health.reports.domain.errors import ReportError

type MediaKind = Literal["pdf", "png", "jpeg"]
type QuarantineReason = Literal[
    "type_mismatch", "truncated", "active_content", "corrupt", "over_bounds", "unverified"
]

MEDIA_TYPES: Final[dict[MediaKind, str]] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
}
_NEUTRAL_DECLARED: Final = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"
_PNG_IEND: Final = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_PDF_TAIL: Final = 1024
_ACTIVE_PDF = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|RichMedia)(?![A-Za-z0-9])")


@dataclass(frozen=True, slots=True)
class MediaDecision:
    state: Literal["accepted", "quarantined"]
    kind: MediaKind
    media_type: str
    reason: QuarantineReason | None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_kind(data: bytes) -> MediaKind | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def _structure_ok(kind: MediaKind, data: bytes) -> bool:
    if kind == "pdf":
        return b"%%EOF" in data[-_PDF_TAIL:]
    if kind == "png":
        return len(data) >= 33 and data[12:16] == b"IHDR" and data.endswith(_PNG_IEND)
    return len(data) >= 4 and data.rstrip(b"\x00").endswith(b"\xff\xd9")


def _declared_agrees(kind: MediaKind, declared_type: str | None) -> bool:
    declared = (declared_type or "").split(";")[0].strip().lower()
    return declared in _NEUTRAL_DECLARED or declared == MEDIA_TYPES[kind]


def _quarantine_reason(kind: MediaKind, data: bytes, declared_type: str | None) -> QuarantineReason | None:
    if not _declared_agrees(kind, declared_type):
        return "type_mismatch"
    if not _structure_ok(kind, data):
        return "truncated"
    if kind == "pdf" and _ACTIVE_PDF.search(data) is not None:
        return "active_content"
    return None


def check_media(data: bytes, *, declared_type: str | None, max_bytes: int) -> MediaDecision:
    """Refuse (typed error) what is not a supported kind or is out of bounds; quarantine what is unsafe."""
    if max_bytes < 1:
        raise ReportError(ErrorCode.CONFIG_INVALID, "report_bound_invalid")
    if len(data) == 0:
        raise ReportError(ErrorCode.INVALID_VALUE, "empty_file")
    if len(data) > max_bytes:
        raise ReportError(ErrorCode.PAYLOAD_TOO_LARGE, "too_large")
    kind = detect_kind(data)
    if kind is None:
        raise ReportError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "unsupported_media")
    reason = _quarantine_reason(kind, data, declared_type)
    return MediaDecision("accepted" if reason is None else "quarantined", kind, MEDIA_TYPES[kind], reason)


def quarantine_after_inspection(decision: MediaDecision, reason: QuarantineReason | None) -> MediaDecision:
    """The decode inspection (contained parser, T2) can only add a quarantine, never lift one."""
    if reason is None or decision.state == "quarantined":
        return decision
    return MediaDecision("quarantined", decision.kind, decision.media_type, reason)
