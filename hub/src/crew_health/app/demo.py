"""`crew-health demo`: the demo-mara-fd19 hub and its operator controls (DEMO-PLAN 4 row 0 and 5), the minimum of
E21's `chc-harness demo reset` and `demo cue` while E21's runner is not on main.

`reset` builds a fresh hub data directory from fixtures/demo-mara-fd19/fixture.json: the station resources, a hub
settings document, the first start (release, time confirmation and the first mission clock version), the SIM roster
with its station and operator principals, and generated credentials. `serve` serves that directory with the
presenter's demo-operator record routed (R-E02-33). `cue` drives a running demo hub over HTTP as the operator.

Every seeded value is read from the file the fixture cites; credentials are generated secrets, never content."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast

from crew_health.app.assembly import BuildCatalog, Runtime, create_app
from crew_health.app.demo_mission import DemoMission, write_environment
from crew_health.app.privacy import DemoOperatorRecord
from crew_health.app.station import HubResources, Origin, bootstrap_release, production
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import OperatorProof
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, RosterMember
from crew_health.kernel.uow import Actor

FIXTURE = "fixtures/demo-mara-fd19/fixture.json"
COPIED = ("contracts", "config", "docs/product", "docs/requests")
CREDENTIALS = "credentials.json"
STATE = "demo.json"


class DemoError(Exception):
    """A fixture or cue the demo cannot use; nothing further was changed."""


def _pointer(document: object, pointer: str) -> object:
    value = document
    for part in [p for p in pointer.split("/") if p]:
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        else:
            raise DemoError("pointer does not resolve: " + pointer)
    return value


@dataclass(frozen=True, slots=True)
class Fixture:
    """fixtures/demo-mara-fd19/fixture.json with every cited value read from its file under `root`."""

    root: Path
    raw: dict[str, object]

    @classmethod
    def load(cls, root: Path, path: Path | None = None) -> Fixture:
        document = json.loads((path or root / FIXTURE).read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise DemoError("fixture is not an object")
        return cls(root, document)

    def cited(self, reference: object) -> object:
        if not isinstance(reference, dict):
            raise DemoError("fixture reference is not an object")
        if "value" in reference:
            return reference["value"]
        document = json.loads((self.root / str(reference["file"])).read_text(encoding="utf-8"))
        return _pointer(document, str(reference.get("pointer", "")))

    def section(self, name: str) -> dict[str, object]:
        value = self.raw[name]
        if not isinstance(value, dict):
            raise DemoError("fixture section is not an object: " + name)
        return cast(dict[str, object], value)

    @property
    def mission_id(self) -> str:
        return str(self.cited(self.raw["mission_id"]))

    @property
    def namespace(self) -> str:
        return str(self.raw["namespace"])

    def members(self) -> tuple[RosterMember, ...]:
        items = self.cited(self.raw["members"])
        if not isinstance(items, list):
            raise DemoError("members are not a list")
        return tuple(
            RosterMember(
                str(item["person_id"]),
                str(item["display_name"]),
                index,
                int(item["on_roster_from_day"]),
                None,
                None if item["medical_officer_rank"] is None else int(item["medical_officer_rank"]),
            )
            for index, item in enumerate(items)
        )

    def principals(self) -> tuple[ProvisionedPrincipal, ...]:
        station = self.cited(self.raw["station"])
        operator = self.section("operator")
        if not isinstance(station, dict):
            raise DemoError("station actor is not an object")
        return (
            ProvisionedPrincipal(str(station["id"]), "station", str(station["display_name"])),
            ProvisionedPrincipal(str(operator["principal_id"]), "operator", str(operator["display_name"])),
        )

    @property
    def operator_id(self) -> str:
        return str(self.section("operator")["principal_id"])

    def mission_day(self) -> tuple[int, str]:
        section = self.section("mission_day")
        template = section["day_label_template"]
        if not isinstance(template, dict):
            raise DemoError("day label template reference")
        profiles = json.loads((self.root / str(template["file"])).read_text(encoding="utf-8"))["profiles"]
        profile = next(p for p in profiles if p["environment_id"] == template["environment_id"])
        return int(str(section["day"])), str(profile["day_label"][str(template["field"])])

    def presenter(self) -> DemoOperatorRecord:
        section = self.section("presenter")
        origin = str(section["origin"])
        if origin not in ("LIVE", "REPLAY", "SIM"):
            raise DemoError("presenter origin")
        return DemoOperatorRecord(
            str(section["persona_person_id"]), str(section["record_id"]), cast(Origin, origin)
        )

    def mission(self, directory: Path) -> DemoMission:
        environment = self.section("environment")
        length = environment.get("planned_length_days")
        return DemoMission(
            directory,
            self.mission_id,
            cast(Origin, self.namespace),
            None if length is None else int(str(length)),
            "",
        )

    @property
    def initial_environment(self) -> str:
        return str(self.section("environment")["initial"])

    @property
    def uncertainty_ms(self) -> int:
        return int(str(self.section("time_confirmation")["uncertainty_ms"]))


# Reset ---------------------------------------------------------------------------------------------------------


def _resources(fixture: Fixture, target: Path) -> HubResources:
    """The repository resources a hub reads, with config/kit-map.json and config/onboard-plans.json replaced by
    the release documents the fixture names (see its `release.basis`)."""
    for name in COPIED:
        shutil.copytree(fixture.root / name, target / name)
    release = fixture.section("release")
    kit_map = fixture.cited(release["kit_map"])
    plans = fixture.cited(release["onboard_plans"])
    if not isinstance(plans, dict):
        raise DemoError("onboard plans are not an object")
    # The plans release check verifies form quotes and rtdc against the saved forms; the package example's few
    # quotes and its one rtdc are not in them, so they are removed as most of its steps already are
    # (hub/tests/app/conftest.py does the same).
    for plan in plans["plans"]:
        for stage in plan["stages"]:
            if stage.get("rtdc") is not None:
                stage["rtdc"] = None
            for step in stage.get("steps", []):
                step["replaces_ground_task"] = None
    for name, value in (("kit-map", kit_map), ("onboard-plans", plans)):
        (target / "config" / (name + ".json")).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    return HubResources(target)


def settings_document(path: Path) -> Path:
    """Every registered setting: the E02 module defaults, the supervisor polling at 50 ms, and every other integer
    at its registered maximum, switches off and text empty (the E01 serve tests' profile)."""
    from crew_health.app.cli import registered_settings
    from crew_health.consent.application.settings import defaults as consent_defaults
    from crew_health.identity.application.settings import defaults as identity_defaults

    chosen: dict[str, object] = {**identity_defaults(), **consent_defaults(), "supervisor.poll_ms": 50}
    entries = []
    for spec in registered_settings():
        value = chosen.get(
            spec.key, {"integer": int(spec.maximum or 0), "boolean": False, "text": ""}[spec.kind]
        )
        entries.append(
            {
                "key": spec.key,
                "kind": spec.kind,
                "unit": None if spec.unit is None else spec.unit.code,
                "minimum": None if spec.minimum is None else str(spec.minimum),
                "maximum": None if spec.maximum is None else str(spec.maximum),
                "basis": "demo-mara-fd19 profile: E02 defaults, otherwise each registered maximum",
                "value": value,
            }
        )
    path.write_text(json.dumps({"settings": entries}), encoding="utf-8")
    return path


def _first_start(runtime: Runtime, fixture: Fixture, credentials: dict[str, str]) -> None:
    from crew_health.app.cli import MissionDay, TimeConfirmation, confirm_time

    if not runtime.serving or runtime.resolver is None:
        raise DemoError("first start did not serve: " + str(runtime.fault_code))
    day, template = fixture.mission_day()
    operator = fixture.operator_id
    confirm_time(runtime, TimeConfirmation(0, fixture.uncertainty_ms, operator, MissionDay(day, template)))
    admin = runtime.resolver.require(IdentityAdmin)
    members = fixture.members()
    principals = fixture.principals()
    document = RosterBootstrap(fixture.mission_id, fixture.namespace, members, principals)  # type: ignore[arg-type]
    console = OperatorProof(Actor("operator", operator), None)
    admin.apply_roster(document, expected_revision=0, operator=console, correlation_id="demo_reset_roster")
    # The operator's own credential last: afterwards every console command needs it proven (E02 4).
    order = [m.person_id for m in members] + [p.principal_id for p in principals if p.kind != "operator"]
    for principal_id in (*order, operator):
        admin.set_credential(
            principal_id,
            credentials[principal_id],
            current_credential=None,
            operator=console,
            client="console",
            correlation_id="demo_reset_credential",
        )


def reset(
    directory: Path, *, root: Path | None = None, fixture_path: Path | None = None
) -> dict[str, object]:
    from crew_health.app.cli import SOURCE, _read_settings, open_database

    resources_root = (root or HubResources.locate().root).resolve()
    fixture = Fixture.load(resources_root, fixture_path)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    resources = _resources(fixture, directory / "resources")
    settings = settings_document(directory / "hub-settings.json")
    write_environment(directory, fixture.initial_environment, source="demo_reset", at=datetime.now(UTC))
    ids = [m.person_id for m in fixture.members()] + [p.principal_id for p in fixture.principals()]
    credentials = {principal_id: secrets.token_urlsafe(9) for principal_id in ids}
    db = open_database(directory / "hub.sqlite", _read_settings(settings))
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.installed(SOURCE),
            contract_path=resources.contracts / "api/openapi.yaml",
            schema_directory=resources.contracts / "api/schemas",
            configure=production(
                resources, origin="SIM", demo=fixture.presenter(), mission=fixture.mission(directory)
            ),
            bootstrap=partial(bootstrap_release, resources),
        )
        runtime = cast(Runtime, app.state.runtime)
        try:
            _first_start(runtime, fixture, credentials)
        finally:
            runtime.shutdown()
    finally:
        db.close()
    path = directory / CREDENTIALS
    path.write_text(json.dumps(credentials, indent=1, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    state = {
        "fixture_id": fixture.raw["fixture_id"],
        "root": str(resources_root),
        "operator": fixture.operator_id,
    }
    (directory / STATE).write_text(json.dumps(state, indent=1), encoding="utf-8")
    return state


# Serve ---------------------------------------------------------------------------------------------------------


def serve(directory: Path, *, host: str, port: int) -> int:
    from crew_health.app.cli import TimeConfirmation
    from crew_health.app.cli import serve as serve_hub

    state = json.loads((directory / STATE).read_text(encoding="utf-8"))
    fixture = Fixture.load(Path(state["root"]))
    return serve_hub(
        database=directory / "hub.sqlite",
        settings=directory / "hub-settings.json",
        host=host,
        port=port,
        resources=HubResources(directory / "resources"),
        origin="SIM",
        confirmation=TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
        demo=fixture.presenter(),
        mission=fixture.mission(directory),
    )


# Cues ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hub:
    base_url: str

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: object = None,
        release: str | None = None,
    ) -> tuple[int, object]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if method != "GET":
            headers["Idempotency-Key"] = uuid.uuid4().hex
        if release is not None:
            headers["Config-Release"] = release
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None


def cue(directory: Path, name: str, *, base_url: str, now: datetime | None = None) -> tuple[int, object]:
    state = json.loads((directory / STATE).read_text(encoding="utf-8"))
    fixture = Fixture.load(Path(state["root"]))
    cues = fixture.section("cues")
    if name not in cues:
        raise DemoError("unknown cue: " + name + " (known: " + ", ".join(sorted(cues)) + ")")
    spec = cast(dict[str, object], cues[name])
    credentials = json.loads((directory / CREDENTIALS).read_text(encoding="utf-8"))
    hub = Hub(base_url.rstrip("/"))
    status, session = hub.call(
        "POST",
        "/v1/sessions",
        body={
            "kind": "operator",
            "person_id": fixture.operator_id,
            "credential": credentials[fixture.operator_id],
        },
    )
    if status != 201 or not isinstance(session, dict):
        raise DemoError("operator sign in refused: " + json.dumps(session))
    token = str(session["token"])
    _, health = hub.call("GET", "/v1/health")
    release = str(cast(dict[str, object], health).get("config_release_id"))
    if name.startswith("ground-link"):
        body = dict(cast(dict[str, object], fixture.cited(spec["request"])))
        if "next_contact_after_hours" in spec:
            at = (now or datetime.now(UTC)) + timedelta(hours=float(str(spec["next_contact_after_hours"])))
            body["next_contact_at"] = at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return hub.call("POST", "/v1/demo/ground-link", token=token, body=body, release=release)
    if name.startswith("model-"):
        body = fixture.cited(spec["request"])
        return hub.call("POST", "/v1/assistant/runtime", token=token, body=body, release=release)
    if name.startswith("environment-"):
        # E23's proposeEnvironmentChange is not on main: the demo control sets the demo mission's environment.
        environment_id = str(spec["environment_id"])
        write_environment(directory, environment_id, source="demo_switch", at=now or datetime.now(UTC))
        return 200, {"environment_id": environment_id, "source": "demo_switch"}
    raise DemoError("cue has no handler: " + name)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="crew-health demo")
    commands = parser.add_subparsers(dest="command", required=True)
    reset_parser = commands.add_parser("reset")
    reset_parser.add_argument("--dir", type=Path, required=True)
    reset_parser.add_argument("--fixture", type=Path, default=None)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--dir", type=Path, required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    cue_parser = commands.add_parser("cue")
    cue_parser.add_argument("name")
    cue_parser.add_argument("--dir", type=Path, required=True)
    cue_parser.add_argument("--hub", default="http://127.0.0.1:8080")
    args = parser.parse_args(list(argv))
    try:
        if args.command == "reset":
            print(json.dumps(reset(args.dir, fixture_path=args.fixture), sort_keys=True))
            return 0
        if args.command == "serve":
            return serve(args.dir, host=args.host, port=args.port)
        status, body = cue(args.dir, args.name, base_url=args.hub)
        print(json.dumps({"status": status, "body": body}, sort_keys=True))
        return 0 if 200 <= status < 300 else 1
    except DemoError as error:
        print(json.dumps({"demo_error": str(error)}), file=sys.stderr)
        return 2
