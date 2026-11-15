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
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from crew_health.app.assembly import BuildCatalog, Runtime, create_app

if TYPE_CHECKING:
    from crew_health.app.cli import Server
from crew_health.app.camera_reports import HubReports
from crew_health.app.demo_mission import DemoMission, read_overlays, write_environment, write_overlays
from crew_health.app.privacy import DemoOperatorRecord
from crew_health.app.station import HubResources, Origin, bootstrap_release, production
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import OperatorProof
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, RosterMember
from crew_health.kernel.uow import Actor
from crew_health.model.application.ports import ModelInstallation

FIXTURE = "fixtures/demo-mara-fd19/fixture.json"
COPIED = (
    "contracts",
    "config",
    "docs/product",
    "docs/requests",
    "eval/scenarios/e23/demo",
    "simulators/habitat/config",
)
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
        camera = self.section("camera")
        habitat = self.section("habitat")
        return (
            ProvisionedPrincipal(str(station["id"]), "station", str(station["display_name"])),
            ProvisionedPrincipal(
                str(camera["adapter_principal_id"]), "adapter", str(camera["adapter_display_name"])
            ),
            *(
                ProvisionedPrincipal(
                    str(feed["adapter_principal_id"]), "adapter", str(feed["adapter_display_name"])
                )
                for feed in cast(list[dict[str, object]], habitat["feeds"])
            ),
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
    """The repository resources a hub reads, copied as they are: the first start activates the repository's own
    config/ release (the fixture's `release.basis`)."""
    if fixture.section("release").get("source") != "config":
        raise DemoError("the demo serves only the repository's config/ release")
    for name in COPIED:
        shutil.copytree(fixture.root / name, target / name)
    return HubResources(target)


# docs/tdd/E28.md operational settings table (team defaults). Their registered maxima are not a consistent profile:
# 1024 escalations per run exceeds storage.max_rows (1000), so loading a run would refuse.
E28_PLANS: dict[str, object] = {
    "plans.max_plans_per_document": 300,
    "plans.max_steps_per_plan": 40,
    "plans.max_observations_per_record": 16,
    "plans.max_rechecks_per_step": 500,
    "plans.max_escalations_per_run": 64,
    "plans.max_text_chars": 2000,
    "plans.parsed_plan_cache_entries": 512,
}


def settings_document(path: Path, camera: dict[str, object] | None = None) -> Path:
    """Every registered setting: the E02 module defaults, the supervisor polling at 250 ms (an idle demo hub at
    50 ms used half a core), the camera worker's address, token file and adapter (`camera`), and every other integer
    at its registered maximum, switches off and text empty (the E01 serve tests' profile)."""
    from crew_health.app.cli import registered_settings
    from crew_health.consent.application.settings import defaults as consent_defaults
    from crew_health.identity.application.settings import defaults as identity_defaults
    from crew_health.sensors.settings import DEFAULTS as sensor_defaults

    chosen: dict[str, object] = {
        **identity_defaults(),
        **consent_defaults(),
        **sensor_defaults,
        "supervisor.poll_ms": 250,
        **E28_PLANS,
        **(camera or {}),
    }
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
                "basis": "demo-mara-fd19 profile: E02 defaults, E28's table, the camera worker row, otherwise each registered maximum",
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
    order = [m.person_id for m in members] + [
        p.principal_id for p in principals if p.kind not in ("operator", "adapter")
    ]
    for principal_id in (*order, operator):
        admin.set_credential(
            principal_id,
            credentials[principal_id],
            current_credential=None,
            operator=console,
            client="console",
            correlation_id="demo_reset_credential",
        )
    _initialize_mission(runtime, fixture)


def _initialize_mission(runtime: Runtime, fixture: Fixture) -> None:
    """UC-M0 on E22/E23's mission module when it is installed: the fixture's mission mode, length and initial
    environment, so sensor channels registered for that environment accept readings (E23 5.3)."""
    from crew_health.clock.application.clock import ClockIdentity
    from crew_health.mission.application.ports import MissionInitializer

    if runtime.resolver is None:
        raise DemoError("hub not resolved")
    initializer = cast(
        "MissionInitializer | None", runtime.resolver.optional(cast(type[object], MissionInitializer))
    )
    if initializer is None:
        return
    length = fixture.section("environment").get("planned_length_days")
    initializer.initialize(
        operator_id=fixture.operator_id,
        mission_mode="SIM",
        planned_length_days=None if length is None else int(str(length)),
        site_node_id=runtime.resolver.require(ClockIdentity).node_id,
        environment_id=fixture.initial_environment,
    )


def reset(
    directory: Path,
    *,
    root: Path | None = None,
    fixture_path: Path | None = None,
    camera_port: int | None = None,
    twin_port: int | None = None,
    habitat_ports: Sequence[int] | None = None,
) -> dict[str, object]:
    from crew_health.app.cli import SOURCE, _read_settings, open_database

    resources_root = (root or HubResources.locate().root).resolve()
    fixture = Fixture.load(resources_root, fixture_path)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    resources = _resources(fixture, directory / "resources")
    camera = directory / "camera"
    camera.mkdir()
    worker_token = camera / "worker.token"
    worker_token.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    worker_token.chmod(0o600)
    section = fixture.section("camera")
    worker_port = camera_port or int(str(section["worker_port"]))
    twin_listen = twin_port or int(str(cast(dict[str, object], section["twin"])["port"]))
    settings = settings_document(
        directory / "hub-settings.json",
        {
            "workers.camera.enabled": True,
            "workers.camera.url": f"http://127.0.0.1:{worker_port}",
            "workers.camera.token_file": str(worker_token),
            "workers.camera.adapter_id": str(section["adapter_principal_id"]),
            # SYSTEM 3 camera worker row: own /health every 5 s; also the probe and camera task pass interval.
            "workers.camera.health_ms": 5000,
            # The stop budget for the hub's workers and the demo's camera companions together.
            "workers.shutdown_timeout_ms": 10000,
            # E23 5.3: the habitat channel registry the replay companion posts into, and the freshness sweep.
            "sensors.channel_registry_path": str(
                directory / "resources" / str(fixture.section("habitat")["registry"])
            ),
            "sensors.deployment_profile": "demo",
            "sensors.sweep_timer_enabled": True,
        },
    )
    write_environment(directory, fixture.initial_environment, source="demo_reset", at=datetime.now(UTC))
    write_overlays(directory, ())
    ids = [m.person_id for m in fixture.members()] + [
        p.principal_id for p in fixture.principals() if p.kind != "adapter"
    ]
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
        "camera_port": worker_port,
        "twin_port": twin_listen,
        # Each habitat feed's adapter health port, in the fixture's feed order; tests pick free ones.
        "habitat_ports": list(habitat_ports)
        if habitat_ports is not None
        else [
            int(str(f["health_port"]))
            for f in cast(list[dict[str, object]], fixture.section("habitat")["feeds"])
        ],
    }
    (directory / STATE).write_text(json.dumps(state, indent=1), encoding="utf-8")
    return state


# Serve ---------------------------------------------------------------------------------------------------------


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise DemoError("uv is not on PATH: the camera worker and twin run as their own uv projects")
    return found


# First `uv run` of the twin syncs its environment; a bound on that one start, not a hub operational setting.
TWIN_START_S = 180
# A companion that fails to start is reported with at most this many of its last output lines.
START_OUTPUT_LINES = 20


def _wait_line(process: subprocess.Popen[str], marker: str, seconds: float, what: str) -> str:
    """The first output line containing `marker`, within `seconds`; the process is left running. A process that
    exits first, or stays silent, fails loudly with `what` named and its last output lines quoted."""
    found: list[str] = []
    tail: deque[str] = deque(maxlen=START_OUTPUT_LINES)
    lock = threading.Lock()
    event = threading.Event()

    def watch() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                with lock:
                    tail.append(line.rstrip())
                if marker in line and not found:
                    found.append(line.strip())
                    event.set()
        event.set()

    threading.Thread(target=watch, daemon=True).start()
    event.wait(seconds)
    if not found:
        with lock:
            output = "\n".join(tail)
        raise DemoError(
            f"{what} did not start (exit code {process.poll()})" + (f":\n{output}" if output else "")
        )
    return found[0]


def _require_free(port: int, what: str) -> None:
    """A companion's port must be free before it starts: a port in use fails loudly with the port named."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise DemoError(
                f"{what} port {port} is in use; stop what holds it or reset with another port"
            ) from None


def habitat_replay(
    directory: Path,
    fixture: Fixture,
    admin: IdentityAdmin,
    proof: OperatorProof,
    *,
    host: str,
    port: int,
    health_ports: Sequence[int],
) -> list[subprocess.Popen[str]]:
    """The habitat adapter replaying the fixture's NASA feeds (E23 5.2) into this hub's ingestReadings, under an
    adapter token issued for this listener. Each reading carries REPLAY from its registered channel."""
    section = fixture.section("habitat")
    habitat = directory / "habitat"
    habitat.mkdir(exist_ok=True)
    feeds = directory / "resources" / str(section["feeds_file"])
    adapters = Path(str(fixture.root)) / "adapters/habitat"
    started: list[subprocess.Popen[str]] = []
    feeds_listed = cast(list[dict[str, object]], section["feeds"])
    if len(health_ports) != len(feeds_listed):
        raise DemoError("one habitat health port per feed")
    for feed, health_port in zip(feeds_listed, health_ports, strict=True):
        view = admin.issue_adapter_token(
            str(feed["adapter_principal_id"]),
            listener=f"{host}:{port}",
            operator=proof,
            correlation_id="demo_serve_habitat",
        )
        _require_free(health_port, "habitat adapter health")
        feed_id = str(feed["feed_id"])
        started.append(
            subprocess.Popen(
                [
                    _uv(),
                    "run",
                    "--project",
                    str(adapters),
                    "crew-health-adapters",
                    str(feed["kind"]),
                    "--feed",
                    str(feeds),
                    "--feed-id",
                    feed_id,
                    "--hub-url",
                    f"http://{host}:{port}",
                    "--queue-file",
                    str(habitat / f"{feed_id}.queue"),
                    "--profile",
                    "demo",
                    "--health-port",
                    str(health_port),
                ],
                env={**os.environ, "CREW_HEALTH_ADAPTER_TOKEN": str(view.token)},
                text=True,
                encoding="utf-8",
            )  # fmt: skip
        )
    return started


def camera_companions(
    directory: Path, fixture: Fixture, state: dict[str, object], *, host: str, port: int
) -> Callable[[Runtime], Callable[[], None]]:
    """Once the hub serves: the camera adapter's token for this listener (E02 3), the camera twin streaming its
    synthetic scene, and the camera worker reading the twin and reporting to the hub. Both stop with the hub."""

    def start(runtime: Runtime) -> Callable[[], None]:
        if runtime.resolver is None:
            raise DemoError("hub not resolved")
        section = fixture.section("camera")
        twin = cast(dict[str, object], section["twin"])
        credentials = json.loads((directory / CREDENTIALS).read_text(encoding="utf-8"))
        admin = runtime.resolver.require(IdentityAdmin)
        proof = admin.console_operator(
            lambda: str(credentials[fixture.operator_id]),
            client="console",
            correlation_id="demo_serve_camera",
        )
        view = admin.issue_adapter_token(
            str(section["adapter_principal_id"]),
            listener=f"{host}:{port}",
            operator=proof,
            correlation_id="demo_serve_camera",
        )
        camera = directory / "camera"
        adapter_token = camera / "adapter.token"
        adapter_token.write_text(str(view.token), encoding="utf-8")
        adapter_token.chmod(0o600)
        root = Path(str(fixture.root))
        processes: list[subprocess.Popen[str]] = []

        grace_s = runtime.database.settings.integer("workers.shutdown_timeout_ms") / 1000

        def stop() -> None:
            """Every companion stops with the hub, within workers.shutdown_timeout_ms in all; any still running
            then is killed."""
            for process in reversed(processes):
                process.terminate()
            deadline = time.monotonic() + grace_s
            for process in processes:
                try:
                    process.wait(max(deadline - time.monotonic(), 0))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        _require_free(int(str(state["twin_port"])), "camera twin")
        _require_free(int(str(state["camera_port"])), "camera worker")
        try:
            processes.append(
                subprocess.Popen(
                    [
                        _uv(),
                        "run",
                        "--project",
                        str(root / "simulators/camera"),
                        "camera-twin",
                        "--port",
                        str(state["twin_port"]),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                )  # fmt: skip
            )
            listening = _wait_line(processes[-1], "listening on", TWIN_START_S, "camera twin").rsplit(" ", 1)[
                -1
            ]
            cameras = [
                {
                    "device_id": c["device_id"],
                    "name": c["name"],
                    "kind": c["kind"],
                    "subject_id": c["subject_id"],
                    "stream": f"{listening}/cameras/{twin['scenario']}/stream.mjpg",
                }
                for c in cast(list[dict[str, object]], section["cameras"])
            ]
            deployment = {
                "host": "127.0.0.1",
                "port": state["camera_port"],
                "hub_url": f"http://{host}:{port}",
                "adapter_id": section["adapter_principal_id"],
                "worker_token_file": str(camera / "worker.token"),
                "adapter_token_file": str(adapter_token),
                "state_dir": str(camera / "state"),
                "cameras": cameras,
            }
            (camera / "deployment.json").write_text(json.dumps(deployment), encoding="utf-8")
            processes.append(
                subprocess.Popen(
                    [
                        _uv(),
                        "run",
                        "--project",
                        str(root / "workers/camera"),
                        "--extra",
                        "capture",
                        "crew-health-camera",
                    ],
                    env={**os.environ, "CREW_HEALTH_CAMERA_DEPLOYMENT": str(camera / "deployment.json")},
                    text=True,
                    encoding="utf-8",
                )  # fmt: skip
            )
            processes.extend(
                habitat_replay(
                    directory,
                    fixture,
                    admin,
                    proof,
                    host=host,
                    port=port,
                    health_ports=[int(p) for p in cast(list[int], state["habitat_ports"])],
                )
            )
        except BaseException:
            stop()
            raise
        return stop

    return start


def _terminated(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def _reports(directory: Path, host: str, port: int) -> HubReports:
    """The camera's attempt reports over this hub's own listener, under the adapter token `demo serve` issues, each
    call bounded by the hub setting workers.camera.timeout_ms."""
    document = json.loads((directory / "hub-settings.json").read_text(encoding="utf-8"))
    timeout_ms = next(
        int(entry["value"]) for entry in document["settings"] if entry["key"] == "workers.camera.timeout_ms"
    )
    return HubReports(f"http://{host}:{port}", directory / "camera" / "adapter.token", timeout_ms / 1000)


def serve(directory: Path, *, host: str, port: int, server: Server | None = None, camera: bool = True) -> int:
    from crew_health.app.cli import TimeConfirmation
    from crew_health.app.cli import serve as serve_hub

    state = json.loads((directory / STATE).read_text(encoding="utf-8"))
    fixture = Fixture.load(Path(state["root"]))
    if camera and threading.current_thread() is threading.main_thread():
        # uvicorn re-raises the SIGTERM it shut down on; as SystemExit it unwinds through the companions' stop
        # instead of killing the process and leaving the twin and the worker running.
        signal.signal(signal.SIGTERM, _terminated)
    return serve_hub(
        ready=camera_companions(directory, fixture, state, host=host, port=port) if camera else None,
        reports=_reports(directory, host, port) if camera else None,
        database=directory / "hub.sqlite",
        settings=directory / "hub-settings.json",
        host=host,
        port=port,
        resources=HubResources(directory / "resources"),
        origin="SIM",
        confirmation=TimeConfirmation(0, fixture.uncertainty_ms, fixture.operator_id),
        demo=fixture.presenter(),
        mission=fixture.mission(directory),
        model=_model_installation(),
        **({} if server is None else {"server": server}),  # type: ignore[arg-type]
    )


def _model_installation() -> ModelInstallation | None:
    """The demo node's staged E17 release, when the presenter names one (DEPLOYMENT: the model is optional).
    CREW_HEALTH_MODEL_DIR holds model/ (artifacts with releases/) and data/ (weights and the active pointer);
    CREW_HEALTH_LLAMA_SERVER is the llama.cpp server binary E17's launch check pins; CREW_HEALTH_MODEL_PORT its
    loopback port. Unset, the hub serves with the assistant unavailable and every safety function unchanged."""
    staged, binary = os.environ.get("CREW_HEALTH_MODEL_DIR"), os.environ.get("CREW_HEALTH_LLAMA_SERVER")
    if not staged or not binary:
        return None
    base = Path(staged)
    port = int(os.environ.get("CREW_HEALTH_MODEL_PORT", "18090"))
    return ModelInstallation(str(base / "model"), str(base / "data"), "demo", binary, port)


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
        if_match: int | None = None,
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
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None


def start_walk(directory: Path, fixture: Fixture, person_id: str, at: datetime) -> dict[str, object]:
    """E23's createOverlay is not on main: the demo control records the fixture's surface walk overlay for one of
    its people as an E23 ActivityOverlay, started now (I-O1: at most one active overlay of a kind per person)."""
    walk = fixture.section("surface_walk")
    people = walk["person_ids"]
    if not isinstance(people, list) or person_id not in people:
        raise DemoError("not on the fixture's surface walk: " + person_id)
    kind = str(walk["overlay"])
    overlays = read_overlays(directory)
    active = next(
        (
            o
            for o in overlays
            if o["person_id"] == person_id and o["overlay"] == kind and o["state"] == "active"
        ),
        None,
    )
    if active is not None:
        return active
    record: dict[str, object] = {
        "overlay_id": "ovl_" + kind + "_" + person_id + "_" + uuid.uuid4().hex[:12],
        "revision": 1,
        "person_id": person_id,
        "overlay": kind,
        "state": "active",
        "planned_start": None,
        "started_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": None,
        "source": {"kind": "operator", "ref_id": None},
        "location": None,
    }
    write_overlays(directory, [*overlays, record])
    return record


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
        body = dict(cast(dict[str, object], fixture.cited(spec["request"])))
        return hub.call("POST", "/v1/assistant/runtime", token=token, body=body, release=release)
    if name.startswith("eva-"):
        return 201, start_walk(directory, fixture, str(spec["person_id"]), now or datetime.now(UTC))
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
    reset_parser.add_argument("--camera-port", type=int, default=None)
    reset_parser.add_argument("--twin-port", type=int, default=None)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--dir", type=Path, required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--no-camera", action="store_true")
    cue_parser = commands.add_parser("cue")
    cue_parser.add_argument("name")
    cue_parser.add_argument("--dir", type=Path, required=True)
    cue_parser.add_argument("--hub", default="http://127.0.0.1:8080")
    args = parser.parse_args(list(argv))
    try:
        if args.command == "reset":
            print(
                json.dumps(
                    reset(
                        args.dir,
                        fixture_path=args.fixture,
                        camera_port=args.camera_port,
                        twin_port=args.twin_port,
                    ),
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "serve":
            return serve(args.dir, host=args.host, port=args.port, camera=not args.no_camera)
        status, body = cue(args.dir, args.name, base_url=args.hub)
        print(json.dumps({"status": status, "body": body}, sort_keys=True))
        return 0 if 200 <= status < 300 else 1
    except DemoError as error:
        print(json.dumps({"demo_error": str(error)}), file=sys.stderr)
        return 2
