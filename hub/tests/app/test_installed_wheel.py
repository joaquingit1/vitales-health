"""R25: the runtime wheel imports and migrates without the development environment."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from crew_health.app import cli

_SMOKE = """
import hashlib
import json
import sys
from importlib.resources import files
from pathlib import Path

import crew_health.app.assembly
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

root = files("crew_health")
modules = sorted(root.iterdir(), key=lambda item: (item.name != "platform", item.name))
migrations = []
schemas = []
db = database(Path(sys.argv[1]))
assert isinstance(db, SQLiteDatabase)
try:
    for module in modules:
        if not module.is_dir():
            continue
        directory = module.joinpath("adapters", "migrations")
        if directory.is_dir():
            for resource in sorted(directory.iterdir(), key=lambda item: item.name):
                if resource.name.endswith(".sql"):
                    raw = resource.read_bytes()
                    db.migrate_tracked(
                        module.name + "." + resource.name[:4],
                        hashlib.sha256(raw).hexdigest(),
                        raw.decode("utf-8"),
                    )
                    migrations.append(module.name + "/adapters/migrations/" + resource.name)
        resources = [(module.joinpath("replica.schema.json"), module.name + "/replica.schema.json")]
        schema_directory = module.joinpath("schemas")
        if schema_directory.is_dir():
            resources.extend(
                (resource, module.name + "/schemas/" + resource.name)
                for resource in sorted(schema_directory.iterdir(), key=lambda item: item.name)
                if resource.is_file() and resource.name.endswith(".json")
            )
        for resource, relative_name in resources:
            if resource.is_file():
                json.loads(resource.read_text(encoding="utf-8"))
                schemas.append(relative_name)
    json.loads(root.joinpath("contracts_gen", "manifest.json").read_text(encoding="utf-8"))
    with db.snapshot() as view:
        count = view.connection.rows("SELECT count(*) FROM platform_migration", (), limit=1)
        assert count == ((len(migrations),),), count
finally:
    db.close()
print(json.dumps({"migrations": sorted(migrations), "schemas": sorted(schemas)}))
"""


def _run(arguments: list[str], directory: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.E01
@pytest.mark.slow
@pytest.mark.trace("R25")
def test_installed_wheel_imports_and_applies_all_packaged_migrations(
    tmp_path: Path, copy_resources: Callable[[Path], Path]
) -> None:
    hub = Path(__file__).resolve().parents[2]
    source = hub / "src/crew_health"
    uv = shutil.which("uv")
    assert uv is not None
    # A wheel builds from a checkout: setup.py packages the contracts, config/ and the package artifacts beside it.
    # The checkout's config/ carries conftest.py's onboard-plans.json, so its first release passes check 14.
    repository = copy_resources(tmp_path / "checkout")
    project = repository / "hub"
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info")
    shutil.copytree(source, project / "src/crew_health", ignore=ignore)
    for name in ("pyproject.toml", "setup.py"):
        shutil.copy2(hub / name, project / name)
    output = tmp_path / "dist"
    _run([uv, "build", str(project), "--wheel", "--out-dir", str(output)], tmp_path)
    wheels = tuple(output.glob("*.whl"))
    assert len(wheels) == 1
    environment = tmp_path / "venv"
    _run([uv, "venv", "--python", sys.executable, str(environment)], tmp_path)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = tmp_path / "runtime.txt"
    _run(
        [
            uv,
            "export",
            "--project",
            str(hub),
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            str(requirements),
        ],
        tmp_path,
    )
    # The frozen export includes the full runtime closure. Check the wheel against it after installation.
    _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "-r", str(requirements), str(wheels[0])],
        tmp_path,
    )
    _run([uv, "pip", "check", "--python", str(python)], tmp_path)
    result = _run([str(python), "-I", "-c", _SMOKE, str(tmp_path / "station")], tmp_path)
    actual = json.loads(result.stdout)
    expected_migrations = sorted(
        path.relative_to(source).as_posix() for path in source.glob("*/adapters/migrations/*.sql")
    )
    expected_schemas = sorted(
        path.relative_to(source).as_posix()
        for pattern in ("*/replica.schema.json", "*/schemas/*.json")
        for path in source.glob(pattern)
    )
    assert expected_migrations
    assert actual == {"migrations": expected_migrations, "schemas": expected_schemas}
    _serves_from_the_wheel(environment, tmp_path)


def settings_file(directory: Path) -> Path:
    # Integers at their registered maximum, switches off and text settings empty.
    entries = [
        {
            "key": spec.key,
            "kind": spec.kind,
            "unit": None,
            "minimum": None if spec.minimum is None else str(spec.minimum),
            "maximum": None if spec.maximum is None else str(spec.maximum),
            "basis": "installed wheel test profile at each registered maximum",
            "value": 50
            if spec.key == "supervisor.poll_ms"
            else {"integer": int(spec.maximum or 0), "boolean": False, "text": ""}[spec.kind],
        }
        for spec in cli.registered_settings()
    ]
    path = directory / "hub-settings.json"
    path.write_text(json.dumps({"settings": entries}), encoding="utf-8")
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serves_from_the_wheel(environment: Path, tmp_path: Path) -> None:
    """The installed console script serves an empty state directory from the wheel's own resources: it bootstraps
    the first release, takes the crew's time confirmation and reports a ready hub."""
    script = environment / ("Scripts/crew-health.exe" if os.name == "nt" else "bin/crew-health")
    state, port = tmp_path / "empty", _free_port()
    state.mkdir()
    settings = settings_file(state)
    command = [
        str(script),
        "serve",
        "--database",
        str(state / "hub.sqlite"),
        "--settings",
        str(settings),
        "--port",
        str(port),
        "--confirm-time-uncertainty-ms",
        "500",
    ]
    process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        body = _health(port, process)
    finally:
        process.terminate()
        output = process.communicate(timeout=60)[0].decode("utf-8", "replace")
    assert body is not None and body["status"] == "ok", (body, output)
    assert str(body["config_release_id"]).startswith("release_"), body


def _health(port: int, process: subprocess.Popen[bytes]) -> dict[str, object] | None:
    for _ in range(600):
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as response:
                body: dict[str, object] = json.loads(response.read())
                if body["status"] == "ok":
                    return body
        except OSError:
            pass
        time.sleep(0.1)
    return None  # the caller reports the process output


@pytest.mark.trace("R25")
def test_wheel_smoke_checks_every_module_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.resources

    root = tmp_path / "package"
    names = (
        "assistant/schemas/assistant-ui.schema.json",
        "extra/schemas/tuning.schema.json",
        "extra/replica.schema.json",
    )
    for name in (*names, "contracts_gen/manifest.json"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(importlib.resources, "files", lambda _: root)
    monkeypatch.setattr(sys, "argv", ["wheel-smoke", str(tmp_path / "state")])
    exec(compile(_SMOKE, "wheel-smoke", "exec"), {})
    assert json.loads(capsys.readouterr().out)["schemas"] == sorted(names)
