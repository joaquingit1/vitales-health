"""uploadReport, getReport and confirmExtraction (openapi.yaml /v1/reports). Names, fields and enums are copied
from the contract. The session comes from the hub middleware (role admission, bounded body, Idempotency-Key);
this router adds the per-object owner check through the workflows. Errors are raised as ProblemError and
rendered by the app's RFC 9457 and envelope handler: no private text reaches a message."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.parser import BytesParser
from email.policy import HTTP

from fastapi import APIRouter, BackgroundTasks, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from crew_health.contracts_gen.schemas.report_schema import Report as WireReport
from crew_health.contracts_gen.schemas.requests_schema import ReportConfirmations
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import SessionContext
from crew_health.reports.application.workflows import ReportWorkflows
from crew_health.reports.domain.confirmation import ConfirmationCommand, Decision, ReportVersion
from crew_health.reports.domain.model import Report

_ETAG = re.compile(r'^"?(\d{1,9}):([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})"?$')
_FIELDS = frozenset({"file", "access_scope", "examination_date", "report_date"})


@dataclass(slots=True)
class ReportsApi:
    workflows: ReportWorkflows | None = None
    origin_of: Callable[[str], str] | None = None
    drain: Callable[[], int] | None = None


def _unavailable() -> ProblemError:
    return ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)


def _session(request: Request) -> SessionContext:
    session: object = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return session


def _instant(report: Report) -> str:
    return report.uploaded_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def etag_of(report: Report) -> str:
    return f'"{report.revision}:{report.source_version_id}"'


def report_wire(report: Report) -> bytes:
    """The contract's Report. Drafts are proposals; a number stays a number and text stays text."""
    body: dict[str, object] = {
        "report_id": report.report_id,
        "file_sha256": report.file_sha256,
        "original_name": report.original_name,
        "media_type": report.media_type,
        "bytes": report.size_bytes,
        "patient_id": report.patient_id,
        "author": report.author,
        "examination_date": None if report.examination_date is None else report.examination_date.isoformat(),
        "report_date": None if report.report_date is None else report.report_date.isoformat(),
        "access_scope": report.access_scope,
        "uploaded_at": _instant(report),
        "extraction_drafts": [
            {
                "draft_id": d.draft_id,
                "source_span": {
                    "page": d.source_span.page,
                    "start": d.source_span.start,
                    "end": d.source_span.end,
                },
                "code": d.code,
                "value": _number(d.value),
                "unit": d.unit,
                "date": None if d.date is None else d.date.isoformat(),
                "extraction_version": d.extraction_version,
                "review_status": d.review_status,
            }
            for d in report.extraction_drafts
        ],
    }
    raw = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    ).encode()
    WireReport.model_validate_json(raw)
    return raw


def _number(value: Decimal | str) -> int | float | str:
    """A finite Decimal as a JSON number (an integer stays an integer); text stays text."""
    if isinstance(value, str):
        return value
    if not value.is_finite():
        raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
    return int(value) if value == value.to_integral_value() else float(value)


def _malformed() -> ProblemError:
    return ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)


@dataclass(frozen=True, slots=True)
class Upload:
    data: bytes
    name: str
    declared_type: str | None
    fields: dict[str, str]


def parse_multipart(content_type: str, body: bytes, *, max_parts: int = 8) -> Upload:
    """The one `file` part and the optional text fields of uploadReport. Unknown, repeated or missing parts are
    malformed. The body is already bounded by the middleware."""
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise _malformed()
    parts = list(message.iter_parts())
    if not 1 <= len(parts) <= max_parts:
        raise _malformed()
    fields: dict[str, str] = {}
    upload: Upload | None = None
    for part in parts:
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or name not in _FIELDS or name in fields or (name == "file" and upload):
            raise _malformed()
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise _malformed()
        if name == "file":
            filename = part.get_filename()
            declared = part.get_content_type() if part["content-type"] else None
            upload = Upload(payload, filename or "", declared, {})
            fields[name] = ""
        else:
            try:
                fields[name] = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise _malformed() from error
    if upload is None:
        raise _malformed()
    return Upload(
        upload.data, upload.name, upload.declared_type, {k: v for k, v in fields.items() if k != "file"}
    )


def _decisions(raw: bytes) -> tuple[Decision, ...]:
    try:
        parsed = ReportConfirmations.model_validate_json(raw)
        loose: object = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except (ValidationError, ValueError, RecursionError) as error:
        raise _malformed() from error
    if not isinstance(loose, dict):
        raise _malformed()
    items = loose["confirmations"]
    out: list[Decision] = []
    for index, item in enumerate(parsed.confirmations):
        original = items[index].get("value") if isinstance(items, list) else None
        out.append(Decision(item.draft_id.root, item.decision, original, item.unit, None))
    return tuple(out)


def _version(request: Request) -> ReportVersion | None:
    header = request.headers.get("If-Match")
    if header is None:
        return None
    found = _ETAG.fullmatch(header.strip())
    if found is None:
        raise _malformed()
    return ReportVersion(int(found.group(1)), found.group(2))


def _headers(report: Report, request: Request) -> dict[str, str]:
    out = {"ETag": etag_of(report)}
    release = request.headers.get("Config-Release")
    if release is not None:
        out["Config-Release"] = release
    return out


def build_router(api: ReportsApi) -> APIRouter:
    router = APIRouter()

    def flows() -> ReportWorkflows:
        if api.workflows is None or api.origin_of is None:
            raise _unavailable()
        return api.workflows

    @router.post("/v1/reports", operation_id="uploadReport", status_code=201)
    async def upload_report(request: Request, background: BackgroundTasks) -> Response:
        session = _session(request)
        workflows = flows()
        key = require_id(request.headers.get("Idempotency-Key", ""))
        body = await request.body()
        parsed = parse_multipart(request.headers.get("content-type", ""), body)
        person = session.person_id or ""
        origin = api.origin_of(person) if api.origin_of is not None else "LIVE"
        report = await run_in_threadpool(
            workflows.upload,
            session,
            key=key,
            data=parsed.data,
            original_name=parsed.name,
            declared_type=parsed.declared_type,
            access_scope=parsed.fields.get("access_scope"),
            examination_date=parsed.fields.get("examination_date"),
            report_date=parsed.fields.get("report_date"),
            origin_mode=origin,
        )
        if api.drain is not None:
            background.add_task(api.drain)
        headers = {**_headers(report, request), "Location": f"/v1/reports/{report.report_id}"}
        response = Response(report_wire(report), 201, headers, "application/json")
        response.background = background
        return response

    @router.get("/v1/reports/{report_id}", operation_id="getReport")
    async def get_report(request: Request, report_id: str) -> Response:
        session = _session(request)
        report = await run_in_threadpool(flows().get_report, session, require_id(report_id))
        return Response(report_wire(report), 200, _headers(report, request), "application/json")

    @router.post("/v1/reports/{report_id}/confirmations", operation_id="confirmExtraction")
    async def confirm_extraction(request: Request, report_id: str) -> Response:
        session = _session(request)
        key = require_id(request.headers.get("Idempotency-Key", ""))
        decisions = _decisions(await request.body())
        person = session.person_id or ""
        command = ConfirmationCommand(_version(request), person, decisions, person, _epoch())
        report = await run_in_threadpool(
            flows().confirm, session, key=key, report_id=require_id(report_id), command=command
        )
        return Response(report_wire(report), 200, _headers(report, request), "application/json")

    return router


def _epoch() -> datetime:
    """Placeholder the workflow replaces with the injected clock before deciding (never used as a time)."""
    return datetime(1970, 1, 1, tzinfo=UTC)
