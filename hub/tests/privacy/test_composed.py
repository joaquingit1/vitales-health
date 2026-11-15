"""E02 composed into the served hub (finding 7): `production` (app/station.py) registers app/privacy.py's adapters
under each consumer's own port class, and every consumer seam answers through E02 over real HTTP.

The station here is production's configure plus the two things production has no provider for yet (E01's record
HTTP services and the ground HubProfile, docs/requests/E02.md). "Admitted" means the request passed E02's gate and
reached the consumer, whose own answer (200, 404, or its own unavailable input on a SIM station) follows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from e02_station import (
    CONSOLE,
    START,
    E02Station,
    Running,
    credential,
    mission_id,
    release_id,
    seed_roster,
    serve,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
    station,
)
from fastapi import Request
from httpx import AsyncClient, Response
from test_http import bearer, problem, run, sign_in

from crew_health.app import privacy
from crew_health.app.assembly import ConfigHttpMetadata, HttpSecurity
from crew_health.app.registry import Resolver
from crew_health.catchup.api.router import RoleGate as CatchupGate
from crew_health.catchup.application.ports import GROUND_LOG_SCOPE
from crew_health.catchup.application.ports import ConsentQueries as CatchupConsent
from crew_health.consent.application.ports import ObservationPartitions, TimeAdmission
from crew_health.consent.domain.grants import GrantRequest
from crew_health.contracts_gen.enums import EventType
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.groundlink.api.router import RoleGate as GroundLinkGate
from crew_health.help.api.router import RoleGate as HelpGate
from crew_health.help.application.ports import RosterPort as HelpRoster
from crew_health.identity.api.router import IdentitySecurity
from crew_health.identity.application.ports import HubProfile
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.identity.domain.session import SignInRequest
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, Audience, EventScope, OutboxEvent
from crew_health.kitmap.api.router import RequireRoles as KitmapRoles
from crew_health.kitmap.application.ports import RosterPort as KitmapRoster
from crew_health.measurement.adapters.repository import SqliteMeasurementStore
from crew_health.measurement.application.commands import POLICY_VERSION, PROJECTION
from crew_health.measurement.application.ports import ConsentQueries as MeasurementConsent
from crew_health.measurement.application.ports import SessionAuthenticator as MeasurementAuthenticator
from crew_health.measurement.application.queries import AUDIENCE_RULE
from crew_health.measurement.application.representation import request_wire
from crew_health.measurement.domain.model import KitCheck, MeasurementRequest, Requester
from crew_health.plans.api.router import RequestAuthenticator as PlansAuthenticator
from crew_health.roles.api.router import RequestAuthenticator as RolesAuthenticator
from crew_health.roles.application.ports import RosterPort as RolesRoster
from crew_health.teach.application.ports import PersonDirectory, RecordRouting
from crew_health.teach.application.ports import RequestAuthenticator as TeachAuthenticator

GROUND = "ground_team"
LISTENER = "127.0.0.1:8000"
REFUSALS = ("UNAUTHENTICATED", "UNAUTHORIZED_SCOPE")


def admitted(response: Response) -> None:
    """The request passed E02's gate: the answer is the consumer's own, never an authentication refusal."""
    assert response.status_code not in (401, 403), response.text
    if response.status_code >= 400:
        assert response.json()["code"] not in REFUSALS, response.text


@pytest.fixture
def hub(tmp_path: Path) -> E02Station:
    return station(tmp_path, profile=HubProfile(ground_sign_in=True))


def composed(hub: E02Station) -> Running:
    running = serve(hub, seed=False)
    principals = (sim_station_principal(), ProvisionedPrincipal(GROUND, "ground", "Ground team"))
    seed_roster(running, sim_bootstrap(principals=principals))
    return running


def resolver(running: Running) -> Resolver:
    value = running.app.state.runtime.resolver
    assert value is not None
    return value


async def tokens(client: AsyncClient) -> dict[str, str]:
    return {
        "mara": await sign_in(client, "mara"),
        "ilya": await sign_in(client, "ilya"),
        "station": await sign_in(
            client, None, kind="station", secret=credential(sim_station_principal().principal_id)
        ),
        "ground": await sign_in(client, None, kind="ground", secret=credential(GROUND)),
    }


@pytest.mark.trace("T-E02-019", "E02-01", "R-E02-9")
def test_production_registers_every_e02_adapter_under_its_consumers_port_class(hub: E02Station) -> None:
    ports = resolver(composed(hub))
    security = ports.require(HttpSecurity)
    assert isinstance(security, IdentitySecurity), "production still serves DenySecurity"
    assert isinstance(ports.require(SessionAuthorizer), privacy.LiveSessions), (
        "production still serves ClosedSessions"
    )
    assert isinstance(ports.require(ConfigHttpMetadata).visibility, privacy.ReplayVisibility)
    expected: dict[object, type[object]] = {
        CatchupGate: privacy.SessionGate,
        HelpGate: privacy.SessionGate,
        GroundLinkGate: privacy.SessionGate,
        KitmapRoles: privacy.SessionGate,
        PlansAuthenticator: privacy.SessionGate,
        RolesAuthenticator: privacy.SessionGate,
        TeachAuthenticator: privacy.HeaderGate,
        MeasurementAuthenticator: privacy.MeasurementGate,
        HelpRoster: privacy.HelpRosterAdapter,
        RolesRoster: privacy.RolesRosterAdapter,
        KitmapRoster: privacy.KitmapRosterAdapter,
        PersonDirectory: privacy.PeopleAdapter,
        RecordRouting: privacy.RoutingAdapter,
        CatchupConsent: privacy.ConsentAdapter,
        MeasurementConsent: privacy.ConsentAdapter,
        ObservationPartitions: privacy.ObservationsAdapter,
        TimeAdmission: privacy.ClockAdmissionAdapter,
    }
    for port, adapter in expected.items():
        assert isinstance(ports.require(port), adapter), port  # type: ignore[call-overload]


@pytest.mark.trace("T-E02-019", "T-E02-009", "E02-01")
def test_help_plans_roles_and_teach_admit_their_contract_roles_and_refuse_the_rest(hub: E02Station) -> None:
    running = composed(hub)

    async def journey(client: AsyncClient) -> None:
        token = await tokens(client)
        listing = await client.get("/v1/help-sessions", headers=bearer(token["mara"]))
        assert listing.status_code == 200, listing.text
        assert (await client.get("/v1/help/broadcasts", headers=bearer(token["station"]))).status_code == 200
        problem(
            await client.get("/v1/help/broadcasts", headers=bearer(token["ground"])),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        assert (await client.get("/v1/plan-runs", headers=bearer(token["ilya"]))).status_code == 200
        problem(await client.get("/v1/plan-runs", headers=bearer(token["ground"])), 403, "UNAUTHORIZED_SCOPE")
        problem(
            await client.get("/v1/help-sessions/hls_1/roles", headers=bearer(token["mara"])), 404, "NOT_FOUND"
        )
        problem(
            await client.get("/v1/help-sessions/hls_1/roles", headers=bearer(token["ground"])),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        problem(
            await client.get("/v1/guided-checks/gck_1", headers=bearer(token["station"])), 404, "NOT_FOUND"
        )
        problem(
            await client.get("/v1/guided-checks/gck_1", headers=bearer(token["ground"])),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        for path in (
            "/v1/help-sessions",
            "/v1/plan-runs",
            "/v1/help-sessions/hls_1/roles",
            "/v1/guided-checks/gck_1",
        ):
            problem(await client.get(path), 401, "UNAUTHENTICATED")
        out = await client.request(
            "DELETE", "/v1/sessions/current", headers=bearer(token["mara"], **{"Idempotency-Key": "mara_out"})
        )
        assert out.status_code == 204, out.text
        for path in ("/v1/help-sessions", "/v1/plan-runs", "/v1/guided-checks/gck_1"):
            problem(await client.get(path, headers=bearer(token["mara"])), 401, "UNAUTHENTICATED")

    run(running, journey)


@pytest.mark.trace("T-E02-019", "T-E02-009", "E02-01")
def test_kitmap_groundlink_measurement_and_catchup_gates_are_e02s(hub: E02Station) -> None:
    """Kit map and ground link pass the gate and then answer with their own missing SIM inputs; measurement
    admits crew and refuses the station by contract role; catchup's log refuses every session E02 can issue
    today, because a ground session names no person (docs/requests/E02.md R-E02-30)."""
    running = composed(hub)

    async def journey(client: AsyncClient) -> None:
        token = await tokens(client)
        for who in ("mara", "station", "ground"):
            admitted(await client.get("/v1/kit-map", headers=bearer(token[who])))
            admitted(await client.get("/v1/ground-link", headers=bearer(token[who])))
        problem(
            await client.get("/v1/measurement-requests/mrq_1", headers=bearer(token["mara"])),
            404,
            "NOT_FOUND",
        )
        problem(
            await client.get("/v1/measurement-requests/mrq_1", headers=bearer(token["station"])),
            403,
            "UNAUTHORIZED_SCOPE",
        )
        for who in ("mara", "ilya", "station", "ground"):
            problem(await client.get("/v1/ground/log", headers=bearer(token[who])), 403, "UNAUTHORIZED_SCOPE")
        for path in ("/v1/kit-map", "/v1/ground-link", "/v1/measurement-requests/mrq_1", "/v1/ground/log"):
            problem(await client.get(path), 401, "UNAUTHENTICATED")

    run(running, journey)


@pytest.mark.trace("T-E02-020", "T-E02-019", "E02-01")
def test_roster_people_and_routing_ports_answer_from_the_current_roster(hub: E02Station) -> None:
    running = composed(hub)
    ports = resolver(running)
    everyone = [member.person_id for member in sim_members()]
    assert [entry.person_id for entry in ports.require(HelpRoster).crew(START)] == everyone  # type: ignore[type-abstract]
    assert [member.person_id for member in ports.require(RolesRoster).crew(START)] == everyone  # type: ignore[type-abstract]
    assert ports.require(KitmapRoster).person_ids() == frozenset(everyone)  # type: ignore[type-abstract]
    people = ports.require(PersonDirectory)  # type: ignore[type-abstract]
    routing = ports.require(RecordRouting)  # type: ignore[type-abstract]
    found = people.person("mara")
    assert found is not None and found.person_id == "mara"
    assert people.person("nobody") is None
    assert routing.record_origin("mara") == "SIM"
    assert routing.record_for("mara", "SIM") == "mara"
    for origin in ("LIVE", "REPLAY"):
        with pytest.raises(ProblemError) as error:
            routing.record_for("mara", origin)
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    members = tuple(member for member in sim_members() if member.person_id != "tomas")
    running.admin.apply_roster(
        sim_bootstrap(
            members=members,
            principals=(sim_station_principal(), ProvisionedPrincipal(GROUND, "ground", "Ground team")),
        ),
        expected_revision=1,
        operator=CONSOLE,
        correlation_id="c",
    )
    assert "tomas" not in [entry.person_id for entry in ports.require(HelpRoster).crew(START)]  # type: ignore[type-abstract]
    assert "tomas" not in ports.require(KitmapRoster).person_ids()  # type: ignore[type-abstract]
    assert people.person("tomas") is None
    with pytest.raises(ProblemError):
        routing.record_origin("tomas")


@pytest.mark.trace("T-E02-007", "T-E02-013", "T-E02-019", "E02-03")
def test_consent_ports_follow_grants_and_events_stop_after_revocation_and_sign_out(hub: E02Station) -> None:
    """catchup and measurement ask E02's consent in their own snapshot; the measurement event audience (the one
    clinical stream gated by consent) admits a grantee only while the grant is active, and a stream's session
    must stay live: after sign out the events endpoint refuses."""
    running = composed(hub)
    ports = resolver(running)
    owner = running.identity.verify_owner("mara", credential("mara"), client="console", correlation_id="c")
    request = _measurement_request(running)
    issued = running.identity.sign_in(
        SignInRequest("crew", "ilya"), credential("ilya"), client="c", listener=LISTENER, correlation_id="c"
    )
    assert issued.token is not None
    ilya = running.identity.authenticate(issued.token, listener=LISTENER)
    rule = running.app.state.runtime.registry.declarations["audiences"][AUDIENCE_RULE]
    audience = Audience(AUDIENCE_RULE, request.request_id, PROJECTION, POLICY_VERSION)

    def grant(purpose: str, object_ref: str) -> str:
        value = GrantRequest(
            "ilya",
            mission_id(),
            "SIM",
            object_ref,
            purpose,
            frozenset({"read"}),
            frozenset({"observation"}),
            START,
            None,
        )
        return running.consent.grant(owner, value, correlation_id="c").grant_id

    def asks() -> tuple[bool, bool, bool]:
        with running.identity.deps.snapshots.snapshot() as view:
            return (
                ports.require(CatchupConsent).allows(  # type: ignore[type-abstract]
                    view, subject_person_id="mara", recipient="ilya", scope=GROUND_LOG_SCOPE
                ),
                ports.require(MeasurementConsent).allows(  # type: ignore[type-abstract]
                    view, subject_person_id="mara", recipient="ilya", scope=("measurement", "heart_rate")
                ),
                rule.admits(view, ilya, audience),
            )

    assert asks() == (False, False, False)
    log, measured = grant(*GROUND_LOG_SCOPE), grant("measurement", "heart_rate")
    assert asks() == (True, True, True)
    running.consent.revoke(owner, measured, expected_revision=1, correlation_id="c")
    running.consent.revoke(owner, log, expected_revision=1, correlation_id="c")
    assert asks() == (False, False, False)

    async def journey(client: AsyncClient) -> None:
        mara = await sign_in(client, "mara")
        seen = await client.get("/v1/events", headers=bearer(mara, Accept="application/json"))
        assert seen.status_code == 200, seen.text
        assert EventType.MEASUREMENT_REQUEST_CHANGED.value in {
            event["type"] for event in seen.json()["events"]
        }
        out = await client.request(
            "DELETE", "/v1/sessions/current", headers=bearer(mara, **{"Idempotency-Key": "mara_signs_out"})
        )
        assert out.status_code == 204, out.text
        problem(
            await client.get("/v1/events", headers=bearer(mara, Accept="application/json")),
            401,
            "UNAUTHENTICATED",
        )

    run(running, journey)
    with running.identity.deps.uows.begin(actor=Actor("engine", "engine_test"), correlation_id="c") as uow:
        running.identity.end_principal_sessions(uow, "ilya", "member_removed")
    with running.identity.deps.snapshots.snapshot() as view, pytest.raises(ProblemError) as ended:
        running.events.require_current(view, ilya)
    assert ended.value.code == ErrorCode.UNAUTHENTICATED


def _measurement_request(running: Running) -> MeasurementRequest:
    """A measurement request for Mara stored and announced exactly as measurement's commands do (commands.py)."""
    deps = running.identity.deps
    request = MeasurementRequest(
        request_id="mrq_1",
        revision=1,
        episode_id=None,
        capability_code="heart_rate",
        reason="routine",
        decision_affected="none",
        kit_check=KitCheck(True, ("camera_station",), (), (), (), "kit can", "0" * 64, "station_leo"),
        requester=Requester("person", "mara", None),
        patient_id="mara",
        operator_id="mara",
        consent_ref=None,
        state="proposed",
        attempts=(),
        result_observation_ids=(),
        body_region=None,
        created_at=START,
        origin_mode="SIM",
        mission_id=mission_id(),
        release_id=release_id(),
        operator_requirement="self",
        methods=("camera_estimate",),
        generation=0,
        unavailable_cause=None,
    )
    store = SqliteMeasurementStore(max_attempts=3, max_late_attempts=3)
    with deps.uows.begin(actor=Actor("person", "mara"), correlation_id="c") as uow:
        store.insert_request(uow.connection, request)
        uow.emit(
            OutboxEvent(
                type=EventType.MEASUREMENT_REQUEST_CHANGED,
                occurred_at=uow.now,
                scope=EventScope("person", "mara", None),
                config_release_id=request.release_id,
                correlation_id=uow.correlation_id,
                data_json=canonical_json(request_wire(request, None)),
                audience=Audience(AUDIENCE_RULE, request.request_id, PROJECTION, POLICY_VERSION),
            )
        )
    return request


def _request(token: str | None) -> Request:
    headers = [] if token is None else [(b"authorization", ("Bearer " + token).encode())]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": headers, "server": ("127.0.0.1", 8000)}
    )


@pytest.mark.trace("T-E02-019", "T-E02-009", "E02-01")
def test_each_consumer_gate_checks_the_contract_roles_itself_without_the_middleware(hub: E02Station) -> None:
    """The gates registered for the consumers decide on their own (a consumer may call them outside E01's
    middleware): the operation's contract roles, a live session on the issuing listener, and nothing for an
    operation the frozen contract does not know."""
    running = composed(hub)
    ports = resolver(running)

    def token(person: str) -> str:
        issued = running.identity.sign_in(
            SignInRequest("crew", person),
            credential(person),
            client="c",
            listener=LISTENER,
            correlation_id="c",
        )
        assert issued.token is not None
        return issued.token

    mara, ilya = token("mara"), token("ilya")
    gate = ports.require(RolesAuthenticator)
    assert gate.session(_request(ilya), "getCrewReadiness").person_id == "ilya"
    for who, operation in ((mara, "getCrewReadiness"), (ilya, "getGroundLog"), (ilya, "noSuchOperation")):
        with pytest.raises(ProblemError) as refused:
            gate.session(_request(who), operation)
        assert refused.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as missing:
        gate.session(_request(None), "getCrewReadiness")
    assert missing.value.code == ErrorCode.UNAUTHENTICATED
    elsewhere = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": _request(ilya).scope["headers"],
            "server": ("10.0.0.9", 9),
        }
    )
    with pytest.raises(ProblemError) as other_listener:
        gate.session(elsewhere, "getCrewReadiness")
    assert other_listener.value.code == ErrorCode.UNAUTHENTICATED
    teach = ports.require(TeachAuthenticator)  # type: ignore[type-abstract]
    measurement = ports.require(MeasurementAuthenticator)  # type: ignore[type-abstract]
    security = ports.require(IdentitySecurity)
    with pytest.raises(ProblemError):
        teach.authenticate({"authorization": "Bearer " + mara}, "getGuidedCheck")  # bound to another listener
    security.authenticate(_request(mara))  # binds this request's listener, as the middleware does
    assert teach.authenticate({"authorization": "Bearer " + mara}, "getGuidedCheck").person_id == "mara"
    with pytest.raises(ProblemError) as ground_only:
        teach.authenticate({"authorization": "Bearer " + mara}, "getGroundLog")
    assert ground_only.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    assert measurement.authenticate({}, "getMeasurementRequest") is None
    assert measurement.authenticate({"authorization": "Bearer " + mara}, "getMeasurementRequest") is not None
    with pytest.raises(ProblemError):
        measurement.authenticate({"authorization": "Bearer " + mara}, "getCrewReadiness")
