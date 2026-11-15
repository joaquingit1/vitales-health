"""GET /v1/supplies and POST /v1/supplies/{supply_id}/uses over FastAPI with E01's real SQLite station, command ledger,
outbox, audit and replication (E32 4.1, 6.1, 10.2)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.contracts_gen.enums import Role
from crew_health.help.application.ports import HelpContext
from crew_health.supplies.application.ports import RunContext, SupplyNeed

from .harness import (
    SCHEMAS,
    Station,
    build_station,
    crew_session,
    example,
    headers,
    lunar_mission,
    use_body,
)

USES = "/v1/supplies/eye_rinse_water/uses"


def validator(schema_file: str) -> Draft202012Validator:
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in sorted(SCHEMAS.glob("*.schema.json"))
    ]
    registry: Registry = Registry().with_resources(resources)
    schema = json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


@pytest.fixture
def station(tmp_path: Path) -> Iterator[Station]:
    built = build_station(tmp_path)
    built.sessions.by_token["crew"] = crew_session()
    built.sessions.by_token["other"] = crew_session("person_b")
    built.sessions.by_token["ground"] = crew_session("ground_1", frozenset({Role.GROUND}))
    yield built


def cover(station: Station, token: str = "crew") -> dict[str, object]:
    response = station.client.get("/v1/supplies", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def entry(body: dict[str, object], supply_id: str) -> dict[str, object]:
    supplies = body["supplies"]
    assert isinstance(supplies, list)
    found = next(e for e in supplies if e["stock"]["supply_id"] == supply_id)
    assert isinstance(found, dict)
    return found


def stock_of(found: dict[str, object]) -> dict[str, object]:
    stock = found["stock"]
    assert isinstance(stock, dict)
    return stock


def audit_envelopes(station: Station) -> list[bytes]:
    return [r[0] for r in station.rows("SELECT envelope FROM platform_audit") if isinstance(r[0], bytes)]


def count(station: Station, table: str) -> int:
    value = station.rows(f"SELECT count(*) FROM {table}")[0][0]
    assert isinstance(value, int)
    return value


@pytest.mark.trace("E32-T-05", "R58", "V91")
def test_one_use_gives_the_lunar_cover_vector_byte_for_byte(station: Station) -> None:
    response = station.client.post(USES, headers=headers(), json=use_body(1))
    assert response.status_code == 201
    body = cover(station)
    expected = example("supplies/cover-lunar.json")
    assert isinstance(expected, dict)
    body.pop("as_of")
    expected = dict(expected)
    expected.pop("as_of")
    assert body == expected
    validator("supply-cover-view.schema.json").validate(cover(station))


@pytest.mark.trace("E32-T-09", "V91", "CONTRACTS 18")
def test_a_use_writes_one_row_one_event_one_audit_and_location(station: Station) -> None:
    response = station.client.post(USES, headers=headers(**{"X-Correlation-Id": "cor-1"}), json=use_body(1))
    assert response.status_code == 201
    body = response.json()
    validator("supply-use.schema.json").validate(body)
    assert response.headers["Location"] == f"{USES}/{body['supply_use_id']}"
    assert response.headers["X-Correlation-Id"] == "cor-1"
    assert response.headers["Config-Release"] == "release_1"
    assert (
        body["quantity"] == 1 and body["origin_mode"] == "SIM" and body["environment_id"] == "lunar_surface"
    )
    assert count(station, "supplies_use") == 1
    events = station.rows("SELECT event_type FROM events_outbox")
    assert events == [("supply.used",)]
    audits = audit_envelopes(station)
    assert len(audits) == 1 and b"supplies.use_recorded" in audits[0]


@pytest.mark.trace("E32-T-01", "EI12", "INV-S9")
def test_a_use_below_zero_is_refused_and_writes_nothing(station: Station) -> None:
    response = station.client.post(USES, headers=headers(), json=use_body(7))
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "SUPPLY_BELOW_ZERO" and problem["field_errors"][0]["field"] == "/quantity"
    assert problem["current_state"] is None
    validator("error.schema.json").validate(problem)
    for table in ("supplies_use", "events_outbox", "platform_audit", "repl_log", "platform_command_ledger"):
        assert count(station, table) == 0
    assert stock_of(entry(cover(station), "eye_rinse_water"))["quantity_now"] == 6


@pytest.mark.trace("E32-T-02", "HZ2")
def test_two_concurrent_last_uses_admit_exactly_one(station: Station) -> None:
    assert station.client.post(USES, headers=headers(key="first-use-1"), json=use_body(5)).status_code == 201
    statuses: list[int] = []

    def use(key: str) -> None:
        for _ in range(50):  # the writer lock may refuse a waiting writer; the client retries a retryable 500
            response = station.client.post(USES, headers=headers(key=key), json=use_body(1))
            if response.status_code != 500:
                statuses.append(response.status_code)
                return
            time.sleep(0.01)

    threads = [threading.Thread(target=use, args=(f"last-use-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [201, 422]
    assert stock_of(entry(cover(station), "eye_rinse_water"))["quantity_now"] == 0


@pytest.mark.trace("E32-T-03", "CONTRACTS 5")
def test_the_same_key_replays_and_a_different_body_or_supply_conflicts(station: Station) -> None:
    first = station.client.post(USES, headers=headers(), json=use_body(1))
    spaced = station.client.post(
        USES,
        headers={**headers(), "Content-Type": "application/json"},
        content=b'{ "client_time": "2026-11-14T12:05:00Z",  "quantity": 1.0 }',
    )
    assert spaced.status_code == 201 and spaced.content == first.content
    assert (
        station.client.post(USES, headers=headers(), json=use_body(2)).json()["code"] == "DUPLICATE_COMMAND"
    )
    other = station.client.post("/v1/supplies/towels/uses", headers=headers(), json=use_body(1))
    assert other.json()["code"] == "DUPLICATE_COMMAND"
    assert count(station, "supplies_use") == 1


@pytest.mark.trace("E32-T-03", "E32-T-19", "charter 6")
def test_a_crash_inside_the_write_leaves_nothing_and_the_retry_writes_once(
    station: Station, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crew_health.supplies.adapters.sqlite_ledger import SqliteLedger

    original = SqliteLedger.register_unit

    def crash(*args: object, **kwargs: object) -> None:
        raise OSError("power lost")

    monkeypatch.setattr(SqliteLedger, "register_unit", crash)
    with pytest.raises(OSError):
        station.client.post(USES, headers=headers(), json=use_body(1))
    for table in ("supplies_use", "events_outbox", "platform_audit", "platform_command_ledger"):
        assert count(station, table) == 0
    monkeypatch.setattr(SqliteLedger, "register_unit", original)
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 201
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 201
    assert count(station, "supplies_use") == 1


@pytest.mark.trace("E32-T-15", "DD5")
def test_a_stale_config_release_is_refused_on_a_direct_use_and_ignored_for_a_help_session(
    station: Station,
) -> None:
    stale = station.client.post(USES, headers=headers(**{"Config-Release": "release_0"}), json=use_body(1))
    assert stale.status_code == 409 and stale.json()["code"] == "CONFIG_VERSION_MISMATCH"
    station.help.contexts["help_1"] = HelpContext(True, "SIM", "lunar_surface")
    helped = station.client.post(
        USES,
        headers=headers(key="help-use-1", **{"Config-Release": "release_0"}),
        json=use_body(1, help_session_id="help_1"),
    )
    assert helped.status_code == 201 and helped.json()["help_session_id"] == "help_1"


@pytest.mark.trace("E32-T-42", "HZ14")
def test_a_refused_help_session_use_leaves_one_evidence_row_also_after_a_retry(station: Station) -> None:
    station.help.contexts["help_1"] = HelpContext(True, "SIM", "lunar_surface")
    body = use_body(9, help_session_id="help_1", step_id="eye_rinse")
    for _ in range(2):
        response = station.client.post(USES, headers=headers(key="offline-use-1"), json=body)
        assert response.status_code == 422 and response.json()["code"] == "SUPPLY_BELOW_ZERO"
    assert count(station, "supplies_use") == 0
    assert count(station, "supplies_use_refused") == 1
    audits = audit_envelopes(station)
    assert len(audits) == 1 and b"supplies.use_refused" in audits[0]


@pytest.mark.trace("E32-T-16", "DD4", "CONTRACTS 9")
def test_partitions_follow_mission_and_mode_and_a_key_never_crosses_missions(station: Station) -> None:
    assert station.client.post(USES, headers=headers(), json=use_body(2)).status_code == 201
    station.mission.facts = lunar_mission("LIVE")
    live = stock_of(entry(cover(station), "eye_rinse_water"))
    assert live["used"] == 0 and live["quantity_now"] == 6  # SIM uses never change a LIVE balance
    again = station.client.post(USES, headers=headers(), json=use_body(2))
    assert again.json()["code"] == "DUPLICATE_COMMAND"  # PLAN row 63: no per-mission key namespace
    station.mission.facts = lunar_mission("SIM")
    assert stock_of(entry(cover(station), "eye_rinse_water"))["used"] == 2


@pytest.mark.trace("E32-T-16", "E32 3.3")
def test_a_run_of_another_environment_or_a_step_with_a_per_use_is_refused(station: Station) -> None:
    needs = {"eye_rinse": (SupplyNeed("plan_eye_injury", "eye_rinse", "eye_rinse_water", None),)}
    station.runs.contexts["run_mars"] = RunContext(True, "plan_eye_injury", "mars_surface", "SIM", needs)
    elsewhere = station.client.post(
        USES, headers=headers(), json=use_body(1, plan_run_id="run_mars", step_id="eye_rinse")
    )
    assert elsewhere.status_code == 422 and elsewhere.json()["field_errors"][0]["field"] == "/plan_run_id"
    from crew_health.kernel.units import Quantity, Unit

    debits = {
        "eye_rinse": (
            SupplyNeed(
                "plan_eye_injury", "eye_rinse", "eye_rinse_water", Quantity(Decimal(1), Unit("rinse"))
            ),
        )
    }
    station.runs.contexts["run_1"] = RunContext(True, "plan_eye_injury", "lunar_surface", "SIM", debits)
    doubled = station.client.post(
        USES, headers=headers(key="key-run-1"), json=use_body(1, plan_run_id="run_1", step_id="eye_rinse")
    )
    assert doubled.status_code == 422 and doubled.json()["field_errors"][0]["field"] == "/step_id"
    station.runs.contexts["run_2"] = RunContext(True, "plan_eye_injury", "lunar_surface", "SIM", needs)
    direct = station.client.post(
        USES, headers=headers(key="key-run-2"), json=use_body(1, plan_run_id="run_2", step_id="eye_rinse")
    )
    assert direct.status_code == 201 and direct.json()["plan_run_id"] == "run_2"


@pytest.mark.trace("E32 10.2", "privacy")
def test_roles_references_and_sessions_are_checked_server_side(station: Station) -> None:
    assert station.client.post(USES, headers=headers(token="ground"), json=use_body(1)).status_code == 403
    assert station.client.get("/v1/supplies", headers={"Authorization": "Bearer ground"}).status_code == 200
    assert station.client.get("/v1/supplies").status_code == 401
    hidden = station.client.post(USES, headers=headers(), json=use_body(1, help_session_id="help_hidden"))
    assert hidden.status_code == 403 and hidden.json()["code"] == "UNAUTHORIZED_SCOPE"
    unknown = station.client.post("/v1/supplies/unknown_supply/uses", headers=headers(), json=use_body(1))
    assert unknown.status_code == 404
    kept = station.client.post(
        "/v1/supplies/eye_wash_set/uses", headers=headers(key="kept-key-1"), json=use_body(1)
    )
    assert kept.status_code == 422 and kept.json()["code"] == "INVALID_VALUE"
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("E32-T-11", "E32-T-22", "EI12")
@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"quantity": 1}, 400),  # client_time missing
        ({"quantity": "1", "client_time": "2026-11-14T12:05:00Z"}, 400),
        ({"quantity": 0, "client_time": "2026-11-14T12:05:00Z"}, 422),
        ({"quantity": 1.0000001, "client_time": "2026-11-14T12:05:00Z"}, 422),  # past max_quantity_scale
        ({"quantity": 2000000, "client_time": "2026-11-14T12:05:00Z"}, 422),  # past max_quantity
        ({"quantity": 1, "quantity_now": 5, "client_time": "2026-11-14T12:05:00Z"}, 400),  # never typed
        (
            {
                "quantity": 1,
                "client_time": "2026-11-14T12:05:00Z",
                "plan_run_id": "r1",
                "help_session_id": "h1",
            },
            422,
        ),
    ],
)
def test_malformed_and_out_of_bound_bodies_are_refused(
    station: Station, body: dict[str, object], status: int
) -> None:
    response = station.client.post(USES, headers=headers(), json=body)
    assert response.status_code == status, response.text
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("E32-T-11", "rule 5")
def test_nan_and_a_missing_key_are_malformed(station: Station) -> None:
    raw = b'{"quantity": NaN, "client_time": "2026-11-14T12:05:00Z"}'
    assert station.client.post(USES, headers=headers(), content=raw).status_code == 400
    no_key = {"Authorization": "Bearer crew"}
    assert station.client.post(USES, headers=no_key, json=use_body(1)).status_code == 400


@pytest.mark.trace("rule 5", "missing input shown as missing")
def test_no_current_mission_fails_loudly_and_never_defaults(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    station.mission.facts = None
    response = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
    assert response.status_code == 500 and response.json()["retryable"] is True
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 500


@pytest.mark.trace("charter 6", "corrupt configuration")
def test_a_release_this_module_cannot_read_serves_no_cover(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import ConfigName

    from .harness import release

    broken = release("release_2", {ConfigName.ONBOARD_PLANS: {"plans": "not a list"}})
    station = build_station(tmp_path, active=broken)
    station.sessions.by_token["crew"] = crew_session()
    response = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"})
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 500
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("AGENTS 3.2", "slow optional provider")
def test_a_slow_help_provider_times_out_without_holding_the_writer(station: Station) -> None:
    gate = threading.Event()

    def slow(session: object, help_session_id: str) -> HelpContext:
        gate.wait(5)
        return HelpContext(True, "SIM", "lunar_surface")

    station.help.context = slow  # type: ignore[method-assign]
    started = time.monotonic()
    response = station.client.post(USES, headers=headers(), json=use_body(1, help_session_id="help_1"))
    assert response.status_code == 500 and response.json()["retryable"] is True
    assert time.monotonic() - started < 2
    assert station.client.post(USES, headers=headers(key="plain-use-1"), json=use_body(1)).status_code == 201
    gate.set()


@pytest.mark.trace("E32-T-12", "EI2")
def test_cover_is_identical_on_repeat_at_the_same_clock(station: Station) -> None:
    station.client.post(USES, headers=headers(), json=use_body(1))
    first = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"}).content
    second = station.client.get("/v1/supplies", headers={"Authorization": "Bearer crew"}).content
    assert first == second


@pytest.mark.trace("E32 10.3", "privacy")
def test_the_event_carries_ids_only_and_no_client_time(station: Station) -> None:
    station.client.post(USES, headers=headers(), json=use_body(1))
    envelope = station.rows("SELECT envelope FROM events_outbox")[0][0]
    text = envelope.decode() if isinstance(envelope, bytes) else str(envelope)
    assert (
        "client_time" not in text and "person_a" in text
    )  # the actor reference, nothing else about the person
