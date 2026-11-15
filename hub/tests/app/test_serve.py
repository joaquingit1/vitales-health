"""`crew-health serve` composes the installed catalog over a real database file, runs the server it is given and
keeps the supervisor alive exactly as long as the server (DEPLOYMENT 7.1, SYSTEM 3 and 5.12). No socket is opened:
the server callable drives the app in process."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from crew_health.app import cli
from crew_health.app.assembly import Runtime, create_app
from crew_health.app.workers import ModuleWorkers
from crew_health.platform.storage import stored_identity

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.3", "SYSTEM.5.12")]
ROOT = Path(__file__).resolve().parents[3]


def settings_file(tmp_path: Path) -> Path:
    entries = []
    for spec in cli.registered_settings():
        # Integers at their registered maximum, switches off and text settings empty.
        assert spec.kind in {"integer", "boolean", "text"} and spec.unit is None
        value: object = {"integer": int(spec.maximum or 0), "boolean": False, "text": ""}[spec.kind]
        entries.append(
            {
                "key": spec.key,
                "kind": spec.kind,
                "unit": None,
                "minimum": None if spec.minimum is None else str(spec.minimum),
                "maximum": None if spec.maximum is None else str(spec.maximum),
                "basis": "serve test profile at each registered maximum",
                "value": 50 if spec.key == "supervisor.poll_ms" else value,
            }
        )
    path = tmp_path / "hub-settings.json"
    path.write_text(json.dumps({"settings": entries}), encoding="utf-8")
    return path


def health(app: FastAPI) -> tuple[int, str]:
    async def read() -> tuple[int, str]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://hub") as client:
            response = await client.get("/v1/health")
            return response.status_code, str(response.json()["status"])

    return asyncio.run(read())


def test_serve_on_an_empty_directory_bootstraps_the_release_and_reports_ready_after_the_time_confirmation(
    tmp_path: Path, station_resources: Path
) -> None:
    """Priority 1: a fresh database reaches serving. The first start installs and activates the release built from
    config/ with its provenance; the crew's time confirmation lifts the first start clock freeze; a second start
    reuses the identity and the release instead of installing again."""
    database, settings = tmp_path / "data/hub.sqlite", settings_file(tmp_path)
    seen: list[tuple[str, int, tuple[int, str], str | None, str | None]] = []

    def server(app: FastAPI, host: str, port: int) -> None:
        runtime: Runtime = app.state.runtime
        seen.append((host, port, health(app), runtime.fault_code, runtime.bootstrapped))

    argv = [
        "serve",
        "--database",
        str(database),
        "--settings",
        str(settings),
        "--resources",
        str(station_resources),
        "--confirm-time-uncertainty-ms",
        "500",
    ]
    assert cli.main(argv, server=server) == 0
    first = stored_identity(database)
    assert first is not None
    assert cli.main(argv, server=server) == 0
    assert stored_identity(database) == first
    assert [entry[:2] for entry in seen] == [(cli.DEFAULT_HOST, cli.DEFAULT_PORT)] * 2
    assert [entry[2:4] for entry in seen] == [((200, "ok"), None)] * 2
    installed = seen[0][4]
    assert installed is not None and installed.startswith("release_") and seen[1][4] is None
    db = cli.open_database(database, cli._read_settings(settings))
    try:
        with db.snapshot() as view:
            rows = view.connection.rows(
                "SELECT r.release_id,r.state,r.note FROM config_release r JOIN config_activation a "
                "ON a.release_id=r.release_id",
                (),
                limit=2,
            )
    finally:
        db.close()
    assert len(rows) == 1 and rows[0][:2] == (installed, "active")
    provenance = json.loads(str(rows[0][2]))
    assert provenance["provenance"] == "first_start"
    assert provenance["documents"]["role-rules"]["path"] == "config/role-rules.json"
    raw = (ROOT / "config/role-rules.json").read_bytes()
    assert provenance["documents"]["role-rules"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["documents"]["onboard-plans"]["path"] == "config/onboard-plans.json"
    assert provenance["documents"]["kit-map"]["path"] == "config/kit-map.json"
    assert cli.main(["doctor", "--database", str(database)]) == 0


def test_serve_from_the_repository_resources_refuses_the_first_release(tmp_path: Path) -> None:
    """The unmodified repository resources do not yet make a valid first release (see conftest.py: D22's kit-map
    suffix fails check 1 and the configured kit-map and onboard-plans disagree), so the hub stays in visible safe
    mode with nothing activated. This test fails once the configured documents validate together."""
    seen: list[tuple[str | None, tuple[int, str]]] = []
    database = tmp_path / "hub.sqlite"
    argv = [
        "serve",
        "--database",
        str(database),
        "--settings",
        str(settings_file(tmp_path)),
        "--confirm-time-uncertainty-ms",
        "500",
    ]

    def server(app: FastAPI, host: str, port: int) -> None:
        seen.append((app.state.runtime.fault_code, health(app)))

    assert cli.main(argv, server=server) == 0
    assert seen == [("CONFIG_INVALID", (200, "degraded"))]


def test_serve_without_the_time_confirmation_stays_degraded_with_clearing_frozen(tmp_path: Path) -> None:
    statuses: list[tuple[int, str]] = []
    argv = ["serve", "--database", str(tmp_path / "hub.sqlite"), "--settings", str(settings_file(tmp_path))]
    assert cli.main(argv, server=lambda app, host, port: statuses.append(health(app))) == 0
    assert statuses == [(200, "degraded")]


def test_serve_refuses_an_invalid_first_release_and_activates_nothing(
    tmp_path: Path, copy_resources: Callable[[Path], Path]
) -> None:
    """A config/ document that fails validation is refused: startup reports CONFIG_INVALID, the hub stays in
    visible safe mode and no release is stored or active."""
    resources = copy_resources(tmp_path / "resources")
    roles = json.loads((resources / "config/role-rules.json").read_bytes())
    roles["roles"] = "not a list"
    (resources / "config/role-rules.json").write_text(json.dumps(roles), encoding="utf-8")
    seen: list[tuple[str | None, tuple[int, str]]] = []

    def server(app: FastAPI, host: str, port: int) -> None:
        seen.append((app.state.runtime.fault_code, health(app)))

    database = tmp_path / "hub.sqlite"
    argv = [
        "serve",
        "--database",
        str(database),
        "--settings",
        str(settings_file(tmp_path)),
        "--resources",
        str(resources),
        "--confirm-time-uncertainty-ms",
        "500",
    ]
    assert cli.main(argv, server=server) == 0
    assert seen == [("CONFIG_INVALID", (200, "degraded"))]
    db = cli.open_database(database, cli._read_settings(settings_file(tmp_path)))
    try:
        with db.snapshot() as view:
            assert view.connection.rows("SELECT count(*) FROM config_release", (), limit=1) == ((0,),)
    finally:
        db.close()


def test_serve_keeps_the_supervisor_running_only_while_the_server_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Supervisor:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    real = create_app

    def with_supervisor(**kwargs: object) -> FastAPI:
        app = real(**kwargs)  # type: ignore[arg-type]
        app.state.runtime.supervisor = Supervisor()
        return app

    def failing(app: FastAPI, host: str, port: int) -> None:
        events.append("serving")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "create_app", with_supervisor)
    argv = [
        "serve",
        "--database",
        str(tmp_path / "hub.sqlite"),
        "--settings",
        str(settings_file(tmp_path)),
        "--resources",
        str(ROOT),
    ]
    with pytest.raises(KeyboardInterrupt):
        cli.main(argv, server=failing)
    assert events == ["start", "serving", "stop"]


def test_serve_rejects_an_incomplete_settings_document_before_touching_the_database(tmp_path: Path) -> None:
    settings = tmp_path / "hub-settings.json"
    settings.write_text(json.dumps({"settings": []}), encoding="utf-8")
    database = tmp_path / "hub.sqlite"
    argv = ["serve", "--database", str(database), "--settings", str(settings)]
    assert cli.main(argv, server=lambda app, host, port: None) == 2
    assert not database.exists()


def test_serve_stops_module_workers_on_exit_and_reports_one_that_does_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped: list[str] = []
    real = create_app

    def stopper(name: str, result: bool) -> Callable[[float], bool]:
        def stop(timeout_s: float) -> bool:
            stopped.append(name)
            return result

        return stop

    def with_workers(**kwargs: object) -> FastAPI:
        app = real(**kwargs)  # type: ignore[arg-type]
        # Probe workers stand beside the hub's own (the roles worker), in a table of their own.
        workers = ModuleWorkers(maximum=4, monotonic_ns=lambda: 0)
        workers.started("probe.clean", stopper("clean", True))
        workers.started("probe.stuck", stopper("stuck", False))
        app.state.runtime.workers = workers
        return app

    monkeypatch.setattr(cli, "create_app", with_workers)
    argv = [
        "serve",
        "--database",
        str(tmp_path / "hub.sqlite"),
        "--settings",
        str(settings_file(tmp_path)),
        "--resources",
        str(ROOT),
    ]
    assert cli.main(argv, server=lambda app, host, port: None) == 1
    assert stopped == ["stuck", "clean"]
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {
        "code": "INTERNAL_ERROR",
        "stuck_workers": ["probe.stuck"],
    }
