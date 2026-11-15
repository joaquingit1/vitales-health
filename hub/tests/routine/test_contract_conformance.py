"""Route identity and produced errors checked against the frozen Routine contract."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml
from fastapi.routing import APIRoute
from httpx import Response

from crew_health.contracts_gen.enums import ERROR_STATUS, ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.routine.api.errors import to_problem
from crew_health.routine.domain import errors

from .http_harness import HttpHarness, headers, problem, validator
from .support import REPO


def operations() -> dict[tuple[str, str], dict[str, object]]:
    spec = yaml.safe_load((REPO / "contracts/api/openapi.yaml").read_text(encoding="utf-8"))
    return {
        (path, method.upper()): op
        for path, methods in spec["paths"].items()
        for method, op in methods.items()
        if isinstance(op, dict) and "Routine" in op.get("tags", ())
    }


def assert_declared_error(response: Response, operation: dict[str, object]) -> None:
    if response.status_code < 400:
        return
    response.read()
    body = response.json()
    validator("error.schema.json").validate(body)
    assert response.status_code == ERROR_STATUS[ErrorCode(body["code"])]
    responses = cast(dict[str, dict[str, object]], operation["responses"])
    declared = {
        code for result in responses.values() for code in cast(list[str], result.get("x-error-codes", []))
    }
    if body["code"] in {"CONFIG_INVALID", "CAPABILITY_UNAVAILABLE"}:
        # The layer C brief explicitly requires these faults, but the immutable base
        # OpenAPI omits them. This assertion records the contract gap, not compliance.
        assert body["code"] not in declared
        assert body["current_state"] is None
        return
    parameters = cast(list[dict[str, str]], operation.get("parameters", []))
    if body["code"] == "CONFIG_VERSION_MISMATCH":
        assert any(p.get("$ref", "").endswith("/ConfigReleaseIn") for p in parameters)
        return
    assert body["code"] in declared | {"INTERNAL_ERROR"}


@pytest.mark.trace("T-E25-015", "T-E25-022")
def test_all_ten_routine_operations_have_exact_contract_paths_methods_and_ids(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    expected = operations()
    mounted = {
        (route.path, method): route.operation_id
        for route in hub.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    assert len(expected) == len(mounted) == 10
    assert mounted == {key: op["operationId"] for key, op in expected.items()}
    for (path, method), operation in expected.items():
        concrete = (
            path.replace("{occurrence_id}", "ro_missing")
            .replace("{item_id}", "missing")
            .replace("{override_id}", "rov_missing")
        )
        response = hub.client.request(method, concrete)
        problem(response, "UNAUTHENTICATED", 401)
        assert_declared_error(response, operation)
        response = hub.client.request(method, concrete, headers=headers(role="operator"))
        problem(response, "UNAUTHORIZED_SCOPE", 403)
        assert_declared_error(response, operation)


@pytest.mark.trace("T-E25-015", "T-E25-018", "T-E25-027")
def test_error_mapping_uses_short_codes_and_never_exposes_private_details() -> None:
    cases: tuple[tuple[Exception, ErrorCode], ...] = (
        (errors.NotFound(), ErrorCode.NOT_FOUND),
        (errors.Unauthorized("private name"), ErrorCode.UNAUTHORIZED_SCOPE),
        (errors.ActionNotAllowed("private note"), ErrorCode.ACTION_NOT_ALLOWED),
        (errors.StaleVersion(42), ErrorCode.STALE_VERSION),
        (errors.InvalidTransition(42), ErrorCode.INVALID_TRANSITION),
        (errors.VersionRequired(), ErrorCode.VERSION_REQUIRED),
        (errors.DuplicateOverride("private_id"), ErrorCode.INVALID_TRANSITION),
        (errors.ConfigVersionMismatch("private_id"), ErrorCode.CONFIG_VERSION_MISMATCH),
        (errors.ConfigInvalid(()), ErrorCode.CONFIG_INVALID),
        (errors.MissionUnavailable("private_id"), ErrorCode.CAPABILITY_UNAVAILABLE),
        (errors.InvalidBody("/until", "private rule"), ErrorCode.MALFORMED_REQUEST),
        (ProblemError(ErrorCode.DUPLICATE_COMMAND, "private note"), ErrorCode.DUPLICATE_COMMAND),
        (RuntimeError("private note"), ErrorCode.INTERNAL_ERROR),
    )
    for error, code in cases:
        result = to_problem(error)
        assert result.code == code
        assert result.current_state is None
        assert "private" not in str(result)
        assert all("private" not in f.code + f.message for f in result.field_errors)
    assert (
        to_problem(errors.InvalidBody("/until", "private rule"), "createOverride").code
        == ErrorCode.INVALID_VALUE
    )
