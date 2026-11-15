"""Real routine station with only its HTTP router and a header authenticator."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.routine.api.router import build_router

from .harness import RoutineHarness, session
from .support import REPO, SCHEMAS


def validator(name: str) -> Draft202012Validator:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in sorted(SCHEMAS.glob("*.schema.json"))
    ]
    registry: Registry = Registry().with_resources(resources)
    return Draft202012Validator(json.loads((SCHEMAS / name).read_text(encoding="utf-8")), registry=registry)


def validate(response: Response, schema: str, status: int = 200) -> dict[str, object]:
    assert response.status_code == status, response.text
    body = cast(dict[str, object], response.json())
    validator(schema).validate(body)
    assert response.headers["X-Correlation-Id"]
    return body


def problem(response: Response, code: str, status: int) -> dict[str, object]:
    body = validate(response, "error.schema.json", status)
    assert body["code"] == code
    assert response.headers["X-Correlation-Id"] == body["correlation_id"]
    assert body["current_state"] is None
    return body


class HeaderAuthenticator:
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext:
        person = headers.get("x-test-person")
        if person is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return session(
            person, roles=frozenset(Role(r) for r in headers.get("x-test-roles", "crew").split(",") if r)
        )


def headers(
    person: str = "person_mara",
    role: str = "crew",
    *,
    key: str | None = None,
    revision: int | None = None,
    release: str | None = None,
) -> dict[str, str]:
    result = {"X-Test-Person": person, "X-Test-Roles": role}
    if key is not None:
        result["Idempotency-Key"] = key
    if revision is not None:
        result["If-Match"] = f'"{revision}"'
    if release is not None:
        result["Config-Release"] = release
    return result


class HttpHarness(RoutineHarness):
    def __init__(self, directory: Path, *, initialize: bool = True) -> None:
        super().__init__(directory, initialize=initialize)
        self.runtime.authenticator = HeaderAuthenticator()
        self.app = FastAPI()
        self.router = build_router(self.runtime, self.runtime, self.runtime.authenticate)
        self.app.include_router(self.router)
        from .test_contract_conformance import operations

        self._operations = operations()
        self.client = TestClient(self.app)
        self.client.event_hooks["response"].append(self._check_error)

    def _check_error(self, response: Response) -> None:
        if response.status_code < 400:
            return
        from .test_contract_conformance import assert_declared_error

        route = next(
            r
            for r in self.router.routes
            if isinstance(r, APIRoute)
            and response.request.method in (r.methods or ())
            and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", r.path), response.request.url.path)
        )
        assert_declared_error(response, self._operations[(route.path, response.request.method)])

    def items(self, path: str, person: str = "person_mara", role: str = "crew") -> list[dict[str, object]]:
        response = self.client.get(path, headers=headers(person, role))
        assert response.status_code == 200, response.text
        items: object = response.json()["items"]
        assert isinstance(items, list)
        return cast(list[dict[str, object]], items)

    def occurrence(self, *, item: str | None = None, person: str = "person_mara") -> dict[str, object]:
        return next(
            r
            for r in self.items("/v1/routine/occurrences", person)
            if (item is None and r["state"] == "due") or r["item_id"] == item
        )

    def manual_body(self, *, bound: bool = False) -> dict[str, object]:
        tasks = self.items("/v1/routine/tasks")
        requested_item = request_example("override-create")["item_id"]
        task = next(
            t
            for t in tasks
            if t["item_id"] == requested_item
            and any(tr["mode"] == "manual" for tr in cast(list[dict[str, object]], t["triggers"]))
        )
        trigger = next(
            tr
            for tr in cast(list[dict[str, object]], task["triggers"])
            if tr["mode"] == ("automatic" if bound else "manual")
        )
        return {
            "item_id": task["item_id"],
            "trigger_index": trigger["index"],
            "person_id": self.roster.roster[0].person_id,
            "until": {"kind": "person_stops"},
        }


def request_example(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((REPO / "contracts/api/examples/requests" / f"{name}.json").read_text(encoding="utf-8"))[
            "value"
        ],
    )
