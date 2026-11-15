"""Typed report refusals (E09). Messages are the contract code only: no private text (name, value, date, hash)
ever reaches an error message, a field error or a repr (AGENTS 1.7, 6 privacy)."""

from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError


class ReportError(ProblemError):
    """A refusal with a closed contract code and a safe detail code (never a value)."""

    def __init__(self, code: ErrorCode, detail_code: str, *, field: str | None = None) -> None:
        errors = () if field is None else (FieldError(field, detail_code, detail_code),)
        super().__init__(code, code.value, field_errors=errors)
        self.detail_code = detail_code


def invalid_value(detail_code: str, *, field: str | None = None) -> ReportError:
    return ReportError(ErrorCode.INVALID_VALUE, detail_code, field=field)


def invalid_unit(detail_code: str, *, field: str | None = None) -> ReportError:
    return ReportError(ErrorCode.INVALID_UNIT, detail_code, field=field)


def identity_mismatch(detail_code: str) -> ReportError:
    return ReportError(ErrorCode.IDENTITY_MISMATCH, detail_code)


def invalid_transition(detail_code: str) -> ReportError:
    return ReportError(ErrorCode.INVALID_TRANSITION, detail_code)


def not_found() -> ReportError:
    return ReportError(ErrorCode.NOT_FOUND, "not_found")


def stale_version() -> ReportError:
    return ReportError(ErrorCode.STALE_VERSION, "stale_version")
