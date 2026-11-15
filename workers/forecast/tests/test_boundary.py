import ast
from pathlib import Path

import pytest

from crew_health_forecast.adapters.settings import SettingsInvalid, WorkerSettings


@pytest.mark.trace("E14-03", "AGENTS-1.2")
def test_worker_has_no_hub_or_downloader_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    forbidden = {"crew_health", "urllib", "requests", "huggingface_hub"}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in forbidden for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden, path
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                assert isinstance(node.args[0], ast.Constant)
                assert node.args[0].value in ("chronos", "torch")


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORT", "x"),
        ("PORT", "0"),
        ("PORT", "65536"),
        ("PORT", "3.5"),
        ("PORT", " 80"),
        ("PORT", "+80"),
        ("MAX_BODY_BYTES", "1023"),
        ("MAX_BODY_BYTES", "1048577"),
        ("MAX_INFLIGHT", "0"),
        ("MAX_INFLIGHT", "5"),
        ("TIMEOUT_MS", "99"),
        ("TIMEOUT_MS", "120001"),
        ("HOST", ""),
        ("HOST", " bad "),
        ("POLICY", ""),
        ("TOKEN_FILE", "\x00"),
        ("WEIGHTS_DIR", ""),
    ],
)
def test_settings_reject_bad_env(name: str, value: str) -> None:
    with pytest.raises(SettingsInvalid):
        WorkerSettings.from_env({"CREW_HEALTH_FORECAST_" + name: value})


@pytest.mark.trace("E14-03", "V69")
def test_settings_defaults_and_explicit_boundaries() -> None:
    settings = WorkerSettings.from_env({})
    assert (
        settings.host,
        settings.port,
        settings.max_body_bytes,
        settings.max_inflight,
        settings.timeout_ms,
    ) == ("127.0.0.1", 8094, 65536, 1, 30000)
    assert settings.token_file is settings.weights_dir is None
    settings.validate_bind(False)
    WorkerSettings(host="::1").validate_bind(False)
    network = WorkerSettings.from_env(
        {
            "CREW_HEALTH_FORECAST_HOST": "0.0.0.0",
            "CREW_HEALTH_FORECAST_PORT": "65535",
            "CREW_HEALTH_FORECAST_MAX_BODY_BYTES": "1048576",
            "CREW_HEALTH_FORECAST_MAX_INFLIGHT": "4",
            "CREW_HEALTH_FORECAST_TIMEOUT_MS": "120000",
        }
    )
    with pytest.raises(SettingsInvalid, match="non_loopback"):
        network.validate_bind(False)
    network.validate_bind(True)


@pytest.mark.trace("E14-03", "V69")
def test_token_file_missing_blank_or_invalid_closes_forecast(tmp_path: Path) -> None:
    path = tmp_path / "token"
    settings = WorkerSettings.from_env({"CREW_HEALTH_FORECAST_TOKEN_FILE": str(path)})
    assert settings.load_token() is None
    for content in ("", "   ", "two tokens", "x" * (settings.max_body_bytes + 1)):
        path.write_text(content, encoding="utf-8")
        assert settings.load_token() is None
    path.write_bytes(b"\xff")
    assert settings.load_token() is None
    path.write_text("secret\n", encoding="utf-8")
    assert settings.load_token() == "secret"


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize("value", [True, "1", 0, 5])
def test_direct_settings_cannot_bypass_bounds(value: object) -> None:
    with pytest.raises(SettingsInvalid):
        WorkerSettings(max_inflight=value)  # type: ignore[arg-type]
