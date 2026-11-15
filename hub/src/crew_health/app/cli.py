"""`crew-health serve` (DEPLOYMENT 7.1, SYSTEM 3 and 5.12): open the hub database, compose every installed module
through create_app, run the HTTP server on the loopback default and keep the maintenance supervisor running for
the life of the process. The other subcommands stay with platform.cli (doctor, verify-store)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import NoReturn, cast

import uvicorn
from fastapi import FastAPI

from crew_health.app.assembly import BuildCatalog, Runtime, create_app
from crew_health.app.camera_reports import HubReports
from crew_health.app.demo_mission import DemoMission
from crew_health.app.installed_modules import REGISTRATIONS
from crew_health.app.privacy import DemoOperatorRecord
from crew_health.app.registry import DeclarationRegistry
from crew_health.app.station import HubResources, Origin, bootstrap_release, production
from crew_health.clock.adapters.system_clock import SystemClock
from crew_health.clock.application.clock import PersistentClocks
from crew_health.clock.application.history import MissionClockService
from crew_health.config.adapters.repository import PinnedRelease
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MissionClock, MissionClockVersion
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.model.application.ports import ModelInstallation, StubInstallation
from crew_health.platform.cli import main as platform_main
from crew_health.platform.domain.errors import fail
from crew_health.platform.settings import ValidatedSettings, specifications
from crew_health.platform.storage import SQLiteDatabase, SystemIds, stored_identity
from crew_health.rules.cli import Console as RulesConsole
from crew_health.rules.cli import run as rules_run

SOURCE = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"  # SYSTEM 3 and DEPLOYMENT 7.1: loopback only by default
DEFAULT_PORT = 8080
# The settings document is bounded before it is parsed: the hub-settings schema allows at most 1024 entries and
# no entry needs more than 1 KiB.
SETTINGS_MAX_BYTES = 1024 * 1024

Server = Callable[[FastAPI, str, int], None]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise fail(ErrorCode.MALFORMED_REQUEST)


def _bound(key: str) -> int:
    spec = {spec.key: spec for spec in specifications()}[key]
    if spec.maximum is None:
        raise fail(ErrorCode.CONFIG_INVALID)
    return int(spec.maximum)


def registered_settings() -> tuple[SettingSpec, ...]:
    """Every setting descriptor the installed modules declare, the inventory a settings document must match."""
    registry = DeclarationRegistry(
        max_ports=_bound("registry.max_ports"), max_hooks=_bound("registry.max_hooks")
    )
    for register in REGISTRATIONS:
        register(registry)
    return tuple(
        value
        for value in registry.declarations.get("settings", {}).values()
        if isinstance(value, SettingSpec)
    )


def _read_settings(path: Path) -> ValidatedSettings:
    try:
        with path.open("rb") as handle:
            raw = handle.read(SETTINGS_MAX_BYTES + 1)
    except OSError:
        raise fail(ErrorCode.CONFIG_INVALID) from None
    return ValidatedSettings.from_json(raw, specs=registered_settings(), max_bytes=SETTINGS_MAX_BYTES)


def open_database(path: Path, settings: ValidatedSettings) -> SQLiteDatabase:
    """Reopen an existing hub under its stored identity, or initialize a new one with a fresh node and
    incarnation. The release handle is a placeholder until the CONFIG phase pins the active release; reading a
    document from it fails closed."""
    ids = SystemIds()
    clock = SystemClock(epoch=ids.new("epoch"), offset=lambda: timedelta(0))
    identity = stored_identity(path)
    node, incarnation = identity or (ids.new("node"), ids.new("incarnation"))
    return SQLiteDatabase(
        path,
        clock=clock,
        ids=ids,
        release=PinnedRelease(ids.new("release"), {}),
        settings=settings,
        origin_node_id=node,
        origin_incarnation=incarnation,
        initialize=identity is None,
    )


def _uvicorn(app: FastAPI, host: str, port: int) -> None:
    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None)).run()


@dataclass(frozen=True, slots=True)
class TimeConfirmation:
    """The crew's confirmation of the hub's time at start (SYSTEM 5.2): the offset to apply to the hub's own
    segment and its uncertainty. Without it a first start keeps clearing frozen and health degraded."""

    offset_ms: int
    uncertainty_ms: int
    confirmed_by: str
    mission_day: MissionDay | None = None


@dataclass(frozen=True, slots=True)
class MissionDay:
    """The mission day the operator confirms with the time at a hub's first start, and its day label template.
    It sets the first MissionClockVersion (docs/requests/E02.md R-E02-32): until one exists no roster member is
    current, so crew sign in, consent and capture all refuse."""

    day: int
    day_label_template: str


def _first_mission_clock(runtime: Runtime, uow: UnitOfWork, value: MissionDay) -> None:
    """Append the first mission clock version, day 1 starting `day - 1` days before the confirmed hub time. A hub
    that already has a version keeps it: later versions are the console's (operator authority), never a restart's."""
    if value.day < 1 or not value.day_label_template or runtime.resolver is None:
        raise fail(ErrorCode.MALFORMED_REQUEST)
    if uow.connection.rows("SELECT clock_version FROM clock_mission_version", (), limit=1):
        return
    clock = runtime.resolver.require(MissionClock)  # type: ignore[type-abstract]
    if not isinstance(clock, MissionClockService):
        raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
    epoch = uow.now - timedelta(days=value.day - 1)
    version = runtime.database.ids.derived("missionclock", runtime.database.origin_node_id)
    clock.append(
        uow,
        MissionClockVersion(version, 1, epoch, epoch, None, value.day_label_template),
        ids=runtime.database.ids,
    )


def confirm_time(runtime: Runtime, confirmation: TimeConfirmation) -> None:
    """Apply the crew's time confirmation as a trusted local operator correction, then sweep at once. A first
    start that also confirms the mission day appends the first mission clock version in the same transaction."""
    if runtime.resolver is None or runtime.supervisor is None or not runtime.serving:
        raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
    db, clocks = runtime.database, runtime.resolver.require(PersistentClocks)
    with db.begin(
        actor=Actor("operator", confirmation.confirmed_by), correlation_id=db.ids.new("startup")
    ) as uow:
        clocks.confirm_hub_time(
            uow,
            correction_id=db.ids.new("correction"),
            offset_ms=confirmation.offset_ms,
            uncertainty_ms=confirmation.uncertainty_ms,
        )
    runtime.supervisor.run_once()
    if confirmation.mission_day is not None:
        with db.begin(
            actor=Actor("operator", confirmation.confirmed_by), correlation_id=db.ids.new("startup")
        ) as uow:
            _first_mission_clock(runtime, uow, confirmation.mission_day)


def serve(
    *,
    database: Path,
    settings: Path,
    host: str,
    port: int,
    resources: HubResources,
    origin: Origin = "SIM",
    confirmation: TimeConfirmation | None = None,
    server: Server = _uvicorn,
    demo: DemoOperatorRecord | None = None,
    mission: DemoMission | None = None,
    model: ModelInstallation | None = None,
    stub: StubInstallation | None = None,
    ready: Callable[[Runtime], Callable[[], None]] | None = None,
    reports: HubReports | None = None,
) -> int:
    db = open_database(database, _read_settings(settings))
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.installed(SOURCE),
            contract_path=resources.contracts / "api/openapi.yaml",
            schema_directory=resources.contracts / "api/schemas",
            configure=production(
                resources, origin=origin, demo=demo, mission=mission, reports=reports, model=model, stub=stub
            ),
            bootstrap=partial(bootstrap_release, resources),
        )
        runtime = cast(Runtime, app.state.runtime)
        try:
            if confirmation is not None and runtime.serving:
                confirm_time(runtime, confirmation)
            # A demo profile starts its companion processes once the hub serves; they stop with it.
            stop = ready(runtime) if ready is not None and runtime.serving else None
            try:
                if runtime.supervisor is not None:
                    runtime.supervisor.start()
                server(app, host, port)
            finally:
                if stop is not None:
                    stop()
        finally:
            stuck = runtime.shutdown()
        if stuck:
            print(
                json.dumps(
                    {"code": ErrorCode.INTERNAL_ERROR.value, "stuck_workers": list(stuck)}, sort_keys=True
                )
            )
            return 1
        return 0
    finally:
        db.close()


def rules_command(arguments: Sequence[str]) -> int:
    """`crew-health rules ...`: the warnings engine's operator console (E24 5.5) over the hub database."""
    parser = _Parser(prog="crew-health rules")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--operator", default="operator_station")
    try:
        known, rest = parser.parse_known_args(list(arguments))
        db = open_database(known.database, _read_settings(known.settings))
    except ProblemError as error:
        print(json.dumps({"code": error.code.value, "retryable": error.retryable}, sort_keys=True))
        return 2
    try:
        console = RulesConsole(sys.stdout, sys.stderr, known.operator, db.ids.new("correlation"))
        bound = min(
            db.settings.integer("rules.pending_max_rows") + 1, db.settings.integer("storage.max_rows")
        )
        return rules_run(rest, db, db.ids, console, limit=bound)
    finally:
        db.close()


def main(argv: Sequence[str] | None = None, *, server: Server = _uvicorn) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["demo"]:
        from crew_health.app.demo import main as demo_main

        return demo_main(arguments[1:])
    if arguments[:1] == ["rules"]:
        return rules_command(arguments[1:])
    if arguments[:1] != ["serve"]:
        return platform_main(arguments)
    parser = _Parser(prog="crew-health")
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--resources", type=Path, default=None)
    parser.add_argument("--origin", choices=("LIVE", "REPLAY", "SIM"), default="SIM")
    parser.add_argument("--confirm-time-uncertainty-ms", type=int, default=None)
    parser.add_argument("--confirm-time-offset-ms", type=int, default=0)
    parser.add_argument("--confirmed-by", default="operator_station")
    parser.add_argument("--confirm-mission-day", type=int, default=None)
    parser.add_argument("--day-label-template", default=None)
    try:
        args = parser.parse_args(arguments)
        confirmation = (
            None
            if args.confirm_time_uncertainty_ms is None
            else TimeConfirmation(
                args.confirm_time_offset_ms,
                args.confirm_time_uncertainty_ms,
                args.confirmed_by,
                None
                if args.confirm_mission_day is None
                else MissionDay(args.confirm_mission_day, args.day_label_template or ""),
            )
        )
        return serve(
            database=args.database,
            settings=args.settings,
            host=args.host,
            port=args.port,
            resources=HubResources.locate() if args.resources is None else HubResources(args.resources),
            origin=cast(Origin, args.origin),
            confirmation=confirmation,
            server=server,
        )
    except ProblemError as error:
        print(json.dumps({"code": error.code.value, "retryable": error.retryable}, sort_keys=True))
        return 2
