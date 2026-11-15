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
from crew_health.app.installed_modules import REGISTRATIONS
from crew_health.app.registry import DeclarationRegistry
from crew_health.app.station import HubResources, Origin, bootstrap_release, production
from crew_health.clock.adapters.system_clock import SystemClock
from crew_health.clock.application.clock import PersistentClocks
from crew_health.config.adapters.repository import PinnedRelease
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.uow import Actor
from crew_health.platform.cli import main as platform_main
from crew_health.platform.domain.errors import fail
from crew_health.platform.settings import ValidatedSettings, specifications
from crew_health.platform.storage import SQLiteDatabase, SystemIds, stored_identity

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


def confirm_time(runtime: Runtime, confirmation: TimeConfirmation) -> None:
    """Apply the crew's time confirmation as a trusted local operator correction, then sweep at once."""
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
) -> int:
    db = open_database(database, _read_settings(settings))
    try:
        app = create_app(
            database=db,
            build=BuildCatalog.installed(SOURCE),
            contract_path=resources.contracts / "api/openapi.yaml",
            schema_directory=resources.contracts / "api/schemas",
            configure=production(resources, origin=origin),
            bootstrap=partial(bootstrap_release, resources),
        )
        runtime = cast(Runtime, app.state.runtime)
        try:
            if confirmation is not None and runtime.serving:
                confirm_time(runtime, confirmation)
            if runtime.supervisor is not None:
                runtime.supervisor.start()
            server(app, host, port)
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


def main(argv: Sequence[str] | None = None, *, server: Server = _uvicorn) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
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
    try:
        args = parser.parse_args(arguments)
        confirmation = (
            None
            if args.confirm_time_uncertainty_ms is None
            else TimeConfirmation(
                args.confirm_time_offset_ms, args.confirm_time_uncertainty_ms, args.confirmed_by
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
