"""FastAPI routes of the medicines module (E32 6.2). Bodies are parsed with the generated contract models; amounts
are read exactly from the JSON text, never through a float. Every error leaves in the contract envelope (D5)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.contracts_gen.schemas.requests_schema import (
    AdministrationCorrectionCreate,
    DoseConfirm,
    DoseProposalCreate,
    LotMovementCreate,
    ManualAdministrationCreate,
    MedicationContextCreate,
    MedicineInspectionCreate,
    WorkingDiagnosisCreate,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import ID_PATTERN
from crew_health.kernel.uow import SessionContext
from crew_health.medicines.api.errors import envelope, malformed
from crew_health.medicines.application import use_cases as uc
from crew_health.medicines.application.codec import dumps
from crew_health.medicines.domain.units import Amount

type Authenticate = Callable[[Request, str], SessionContext]
type DepsOf = Callable[[], uc.Deps]

CMO = frozenset({Role.CREW_MEDICAL_OFFICER})
CMO_CREW = frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW})
CMO_CREW_OPERATOR = frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW, Role.OPERATOR})
CMO_OPERATOR = frozenset({Role.CREW_MEDICAL_OFFICER, Role.OPERATOR})
ENVIRONMENTS = frozenset({"station_leo", "transit", "lunar_surface", "mars_surface"})  # common.schema
_KEY_MIN, _KEY_MAX = 8, 128  # openapi IdempotencyKey
_BODY_MAX = 65536  # bytes; a context statement with its 200 items is the largest body


def _correlation(request: Request, deps: DepsOf) -> str:
    value = request.headers.get("X-Correlation-Id")
    if value is not None and re.fullmatch(ID_PATTERN, value) is not None:
        return value
    try:
        return deps().ids.new("cor")
    except ProblemError:
        return "cor-medicines-unbound"


def _session(
    authenticate: Authenticate, request: Request, operation: str, roles: frozenset[Role]
) -> SessionContext:
    session = authenticate(request, operation)
    if not session.roles & roles:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    return session


def _no_constant(text: str) -> object:
    raise ValueError("NaN or infinity: " + text)


def _parse[M: BaseModel](raw: bytes, model: type[M]) -> tuple[M, dict[str, object], str]:
    """The generated model checks the shape; the raw value (exact decimals) and its canonical sha256 come along."""
    if len(raw) > _BODY_MAX:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    try:
        value = json.loads(raw, parse_float=Decimal, parse_constant=_no_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise malformed("body is not JSON", "/") from error
    if not isinstance(value, dict):
        raise malformed("body is not an object", "/")
    try:
        parsed = model.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        pointer = "/" + "/".join(str(p) for p in first.get("loc", ()))
        raise malformed(str(first.get("msg", "invalid body")), pointer) from error
    return parsed, value, hashlib.sha256(dumps(value).encode()).hexdigest()


def _amount(value: Mapping[str, object], field: str) -> Amount:
    raw = value.get(field)
    if not isinstance(raw, dict):
        raise malformed(f"{field} is an amount", f"/{field}")
    number = raw.get("value")
    if isinstance(number, bool) or not isinstance(number, int | Decimal):
        raise malformed("value is a number", f"/{field}/value")
    return Amount(Decimal(number), str(raw.get("ucum")))


def _key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if key is None or not _KEY_MIN <= len(key) <= _KEY_MAX:
        raise malformed("Idempotency-Key missing or malformed", "/Idempotency-Key")
    return key


def _reply(result: uc.Result, correlation: str, location: str | None = None) -> Response:
    if result.status == 409:  # a committed supersede or expiry: the proposal travels as the current state
        problem = ProblemError(
            ErrorCode.INVALID_TRANSITION, "the proposal is no longer open", current_state=result.body
        )
        return envelope(problem, correlation)
    headers = {"X-Correlation-Id": correlation}
    if location is not None and result.resource_id is not None:
        headers["Location"] = f"{location}/{result.resource_id}"
    return Response(
        dumps(result.body), status_code=result.status, media_type="application/json", headers=headers
    )


def _ok(body: Mapping[str, object], correlation: str) -> Response:
    return Response(
        dumps(body), status_code=200, media_type="application/json", headers={"X-Correlation-Id": correlation}
    )


def build_router(deps: DepsOf, authenticate: Authenticate) -> APIRouter:
    router = APIRouter()

    def guarded(
        fn: Callable[[Request, str], Awaitable[Response]],
    ) -> Callable[[Request], Awaitable[Response]]:
        async def run(request: Request) -> Response:
            correlation = _correlation(request, deps)
            try:
                return await fn(request, correlation)
            except ProblemError as error:
                return envelope(error, correlation)

        return run

    async def command[T: BaseModel](
        request: Request, correlation: str, operation: str, roles: frozenset[Role], model: type[T]
    ) -> tuple[uc.Deps, SessionContext, uc.Command, T, dict[str, object]]:
        raw = await request.body()
        session = _session(authenticate, request, operation, roles)
        key = _key(request)
        parsed, value, sha = _parse(raw, model)
        return deps(), session, uc.Command(key, sha, correlation), parsed, value

    @guarded
    async def list_lots(request: Request, correlation: str) -> Response:
        session = _session(authenticate, request, "listMedicineLots", CMO_CREW_OPERATOR)
        environment_id = request.query_params.get("environment_id")
        if environment_id is not None and environment_id not in ENVIRONMENTS:
            raise malformed("environment_id is not an environment", "/environment_id")
        return _ok(uc.lots_view(deps(), session, environment_id), correlation)

    @guarded
    async def inspection(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "recordMedicineInspection", CMO_CREW, MedicineInspectionCreate
        )
        result = uc.record_inspection(
            d, session, cmd, body.lot_id, body.finding, body.note or "", body.client_time.root
        )
        return _reply(result, correlation)

    def unavailable(operation: str, roles: frozenset[Role]) -> Callable[[Request], Awaitable[Response]]:
        """Assays and scheduled assessments are not built in this release (gaps in the E32 report): refused, never
        accepted and dropped."""

        async def refuse(request: Request, correlation: str) -> Response:
            _session(authenticate, request, operation, roles)
            raise ProblemError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "not available in this release", retryable=False
            )

        return guarded(refuse)

    @guarded
    async def context_create(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "recordMedicationContext", CMO, MedicationContextCreate
        )
        person_id = request.path_params["person_id"]
        result = uc.record_context(
            d, session, cmd, person_id, body.part, body.state, body.items or [], body.client_time.root
        )
        return _reply(result, correlation)

    @guarded
    async def context_get(request: Request, correlation: str) -> Response:
        session = _session(authenticate, request, "getMedicationContext", CMO_CREW)
        return _ok(uc.get_context(deps(), session, request.path_params["person_id"]), correlation)

    @guarded
    async def diagnosis(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "recordWorkingDiagnosis", CMO, WorkingDiagnosisCreate
        )
        result = uc.record_diagnosis(
            d,
            session,
            cmd,
            request.path_params["episode_id"],
            body.subject_person_id.root,
            body.condition_id,
            body.basis_text or "",
            body.certainty_text or "",
            None if body.supersedes is None else body.supersedes.root,
            body.client_time.root,
        )
        return _reply(result, correlation)

    @guarded
    async def options(request: Request, correlation: str) -> Response:
        session = _session(authenticate, request, "listTreatmentOptions", CMO)
        return _ok(
            uc.treatment_options(deps(), session, request.path_params["working_diagnosis_id"]), correlation
        )

    @guarded
    async def proposal_create(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "createDoseProposal", CMO, DoseProposalCreate
        )
        result = uc.create_proposal(
            d,
            session,
            cmd,
            body.working_diagnosis_id.root,
            body.product_id,
            body.lot_id,
            body.path == "labeled",
            body.client_time.root,
        )
        return _reply(result, correlation, "/v1/dose-proposals")

    @guarded
    async def proposal_get(request: Request, correlation: str) -> Response:
        session = _session(authenticate, request, "getDoseProposal", CMO_CREW)
        return _ok(uc.get_proposal(deps(), session, request.path_params["proposal_id"]), correlation)

    @guarded
    async def confirm(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "confirmDoseProposal", CMO_CREW, DoseConfirm
        )
        if_match = request.headers.get("If-Match")
        if if_match is None:
            raise ProblemError(ErrorCode.VERSION_REQUIRED, ErrorCode.VERSION_REQUIRED.value)
        result = uc.confirm_proposal(
            d,
            session,
            cmd,
            request.path_params["proposal_id"],
            if_match,
            body.lot_id,
            [uc.Override(o.alert_id, o.reason_code, o.note or "") for o in body.overrides],
            [a.root for a in body.acknowledged_alert_ids],
            body.client_time.root,
        )
        return _reply(result, correlation, "/v1/medication-administrations")

    @guarded
    async def manual(request: Request, correlation: str) -> Response:
        d, session, cmd, body, value = await command(
            request, correlation, "recordManualAdministration", CMO_CREW, ManualAdministrationCreate
        )
        result = uc.record_manual(
            d,
            session,
            cmd,
            body.subject_person_id.root,
            body.product_id,
            body.lot_id,
            _amount(value, "amount"),
            body.administered_at.root,
            body.reason_text,
        )
        return _reply(result, correlation, "/v1/medication-administrations")

    @guarded
    async def administrations(request: Request, correlation: str) -> Response:
        session = _session(authenticate, request, "listMedicationAdministrations", CMO_CREW)
        subject = request.query_params.get("subject")
        if subject is None or re.fullmatch(ID_PATTERN, subject) is None:
            raise malformed("subject is a person id", "/subject")
        return _ok(uc.list_administrations(deps(), session, subject), correlation)

    @guarded
    async def correction(request: Request, correlation: str) -> Response:
        d, session, cmd, body, _ = await command(
            request, correlation, "correctMedicationAdministration", CMO_CREW, AdministrationCorrectionCreate
        )
        result = uc.correct_administration(
            d, session, cmd, request.path_params["administration_id"], body.reason_text
        )
        return _reply(result, correlation)

    @guarded
    async def movement(request: Request, correlation: str) -> Response:
        d, session, cmd, body, value = await command(
            request, correlation, "recordLotMovement", CMO_CREW, LotMovementCreate
        )
        result = uc.record_movement(
            d,
            session,
            cmd,
            request.path_params["lot_id"],
            body.kind,
            _amount(value, "quantity"),
            body.client_time.root,
        )
        return _reply(result, correlation)

    @guarded
    async def fhir(request: Request, correlation: str) -> Response:
        """Brief decision 13: the FHIR export (B07) belongs to E29 and is not available from this module."""
        _session(authenticate, request, "exportMedicationAdministration", CMO_CREW)
        raise ProblemError(ErrorCode.CONTEXT_INAPPLICABLE, "FHIR export is not available", retryable=False)

    routes: tuple[tuple[str, str, str, Callable[[Request], Awaitable[Response]]], ...] = (
        ("GET", "/v1/medicines/lots", "listMedicineLots", list_lots),
        ("POST", "/v1/medicines/inspections", "recordMedicineInspection", inspection),
        ("POST", "/v1/medicines/assays", "recordMedicineAssay", unavailable("recordMedicineAssay", CMO)),
        (
            "POST",
            "/v1/medicines/assessments",
            "scheduleMedicineAssessment",
            unavailable("scheduleMedicineAssessment", CMO_OPERATOR),
        ),
        ("POST", "/v1/people/{person_id}/medication-context", "recordMedicationContext", context_create),
        ("GET", "/v1/people/{person_id}/medication-context", "getMedicationContext", context_get),
        ("POST", "/v1/episodes/{episode_id}/working-diagnoses", "recordWorkingDiagnosis", diagnosis),
        (
            "GET",
            "/v1/working-diagnoses/{working_diagnosis_id}/treatment-options",
            "listTreatmentOptions",
            options,
        ),
        ("POST", "/v1/dose-proposals", "createDoseProposal", proposal_create),
        ("GET", "/v1/dose-proposals/{proposal_id}", "getDoseProposal", proposal_get),
        ("POST", "/v1/dose-proposals/{proposal_id}/confirm", "confirmDoseProposal", confirm),
        ("POST", "/v1/medication-administrations", "recordManualAdministration", manual),
        ("GET", "/v1/medication-administrations", "listMedicationAdministrations", administrations),
        (
            "POST",
            "/v1/medication-administrations/{administration_id}/corrections",
            "correctMedicationAdministration",
            correction,
        ),
        ("POST", "/v1/medicines/lots/{lot_id}/movements", "recordLotMovement", movement),
        (
            "GET",
            "/v1/medication-administrations/{administration_id}/fhir",
            "exportMedicationAdministration",
            fhir,
        ),
    )
    for method, path, operation_id, endpoint in routes:
        router.add_api_route(path, endpoint, methods=[method], operation_id=operation_id)
    return router
