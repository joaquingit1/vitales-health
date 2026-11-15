"""Process startup uses validated files and preserves source and failure evidence."""

import argparse
import json
import runpy
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_health_adapter_oximeter import __main__ as cli
from crew_health_device.adapters.ble_source import BleakFrameSource
from crew_health_device.adapters.recorded_source import RecordedFrameSource
from crew_health_device.application.settings import descriptors

from .helpers import profile_document, settings


@pytest.mark.trace("E07-03", "AGENTS 4")
def test_sim_source_option_selects_the_twin_and_labels_it_sim() -> None:
    factory, origin = cli._source("sim:hypoxia_drop:7", settings())
    assert origin == "SIM"
    assert isinstance(factory(), BleakFrameSource)
    for spec in ("sim:normal", "sim:storm:7", "radio:7"):
        with pytest.raises(ValueError):
            cli._source(spec, settings())


@pytest.fixture
def startup_args(tmp_path: Path) -> argparse.Namespace:
    options = asdict(settings())
    document = {
        descriptor.key: {
            "value": options[descriptor.key.removeprefix("adapter.")],
            "unit": descriptor.unit,
            "basis": descriptor.basis,
            "to_be_set_by": "test operator",
        }
        for descriptor in descriptors()
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(document), encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile_document()), encoding="utf-8")
    token_path = tmp_path / "token.txt"
    token_path.write_text("local-test-token", encoding="utf-8")
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    return argparse.Namespace(
        settings=str(settings_path),
        profile=str(profile_path),
        token_file=str(token_path),
        hub_url="http://127.0.0.1:8080/",
        adapter_id="oximeter-1",
        source="sim:normal:7",
        journal=str(journal_path),
        port=8123,
    )


def arguments(args: argparse.Namespace) -> list[str]:
    return [
        "oximeter",
        *(part for key, value in vars(args).items() for part in ("--" + key.replace("_", "-"), str(value))),
    ]


def recording_file(tmp_path: Path) -> str:
    path = tmp_path / "recording.json"
    path.write_text(
        json.dumps(
            {
                "origin": "REPLAY",
                "provenance": "E07 startup test with no measurements",
                "frames": [],
            }
        ),
        encoding="utf-8",
    )
    return "recording:" + str(path)


@pytest.mark.trace("E07-03", "AGENTS 3.7")
@pytest.mark.parametrize(
    "url",
    [
        "http://",
        "https://",
        "file:///tmp/hub",
        "relative",
        "https://user:pass@host",
        "http://host?query=private",
        "http://host#fragment",
    ],
)
def test_startup_rejects_invalid_hub_urls(url: str) -> None:
    with pytest.raises(ValueError, match="invalid_hub_url"):
        cli._hub_url(url)


@pytest.mark.trace("E07-03", "AGENTS 3.7")
@pytest.mark.parametrize("error", ["missing", "noninteger_port", "unknown_option"])
def test_argument_errors_exit_with_invalid_arguments(
    monkeypatch: pytest.MonkeyPatch,
    startup_args: argparse.Namespace,
    error: str,
) -> None:
    argv = arguments(startup_args)
    if error == "missing":
        argv = argv[:1]
    elif error == "noninteger_port":
        argv[argv.index("--port") + 1] = "not-an-integer"
    else:
        argv.append("--unknown")
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == "INVALID_ARGUMENTS"


@pytest.mark.trace("E07-03", "R26", "AGENTS 4")
@pytest.mark.parametrize(
    "source,origin",
    [
        ("ble:device-address", "LIVE"),
        ("recording", "REPLAY"),
        ("sim:normal:7", "SIM"),
    ],
)
@pytest.mark.parametrize("port", [1, 65535])
def test_run_starts_loopback_app_with_real_files(
    monkeypatch: pytest.MonkeyPatch,
    startup_args: argparse.Namespace,
    tmp_path: Path,
    source: str,
    origin: str,
    port: int,
) -> None:
    startup_args.source = recording_file(tmp_path) if source == "recording" else source
    startup_args.port = port
    served: list[tuple[FastAPI, str, int]] = []

    def serve(app: FastAPI, *, host: str, port: int) -> None:
        served.append((app, host, port))
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["adapter_id"] == startup_args.adapter_id
        assert body["state"] == "quiet"
        manifest = body["manifest"]
        assert manifest["family"] == "oximeter"
        assert manifest["profile"]["device_label"] == profile_document()["device_label"]
        # A device (real or its twin) is a connected source, unavailable until it connects; a file is recorded.
        operation = manifest["operations"][1 if origin == "REPLAY" else 0]
        assert operation["origin"] == origin
        assert operation["status"] == ("available" if origin == "REPLAY" else "unavailable")

    monkeypatch.setattr(uvicorn, "run", serve)
    cli._run(startup_args)
    assert len(served) == 1
    assert served[0][1:] == ("127.0.0.1", port)
    assert list(Path(startup_args.journal).iterdir()) == []


@pytest.mark.trace("E07-03", "AGENTS 4")
@pytest.mark.parametrize("source", ["ble:device-address", "recording", "sim:normal:7"])
def test_source_factory_creates_fresh_sources(tmp_path: Path, source: str) -> None:
    spec = recording_file(tmp_path) if source == "recording" else source
    factory, origin = cli._source(spec, settings())
    first, second = factory(), factory()
    assert first is not second
    if source == "recording":
        assert origin == "REPLAY"
        assert isinstance(first, RecordedFrameSource)
        first.open(1000)
        assert first.next_frame(1000) is None
        first.close()
    else:
        assert origin == ("SIM" if source.startswith("sim:") else "LIVE")
        assert isinstance(first, BleakFrameSource)


@pytest.mark.trace("E07-03", "AGENTS 3.7")
@pytest.mark.parametrize(
    "invalid",
    [
        "wrong_family",
        "port_zero",
        "port_high",
        "empty_token",
        "whitespace_token",
        "long_token",
        "bad_hub_url",
        "hub_credentials",
        "unknown_source",
        "empty_ble",
        "bad_sim",
        "unknown_sim",
    ],
)
def test_invalid_startup_fails_before_serving(
    monkeypatch: pytest.MonkeyPatch,
    startup_args: argparse.Namespace,
    invalid: str,
) -> None:
    if invalid == "wrong_family":
        document = profile_document()
        document["family"] = "cuff"
        Path(startup_args.profile).write_text(json.dumps(document), encoding="utf-8")
    elif invalid in ("port_zero", "port_high"):
        startup_args.port = 0 if invalid == "port_zero" else 65536
    elif invalid in ("empty_token", "whitespace_token", "long_token"):
        token = {
            "empty_token": "",
            "whitespace_token": "local test\ttoken",
            "long_token": "x" * (settings().profile_max_bytes + 1),
        }[invalid]
        Path(startup_args.token_file).write_text(token, encoding="utf-8")
    elif invalid in ("bad_hub_url", "hub_credentials"):
        startup_args.hub_url = "relative" if invalid == "bad_hub_url" else "https://user:pass@host"
    else:
        startup_args.source = {
            "unknown_source": "radio:7",
            "empty_ble": "ble:",
            "bad_sim": "sim:normal",
            "unknown_sim": "sim:storm:7",
        }[invalid]
    served: list[FastAPI] = []

    def serve(app: FastAPI, *, host: str, port: int) -> None:
        served.append(app)

    monkeypatch.setattr(uvicorn, "run", serve)
    monkeypatch.setattr(sys, "argv", arguments(startup_args))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == "ADAPTER_START_OR_RUNTIME_FAILED"
    assert served == []
    assert list(Path(startup_args.journal).iterdir()) == []


@pytest.mark.trace("E07-03")
def test_module_entry_invokes_main(
    monkeypatch: pytest.MonkeyPatch,
    startup_args: argparse.Namespace,
) -> None:
    served: list[tuple[FastAPI, str, int]] = []

    def serve(app: FastAPI, *, host: str, port: int) -> None:
        served.append((app, host, port))
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["adapter_id"] == startup_args.adapter_id

    monkeypatch.setattr(uvicorn, "run", serve)
    monkeypatch.setattr(sys, "argv", arguments(startup_args))
    monkeypatch.delitem(sys.modules, cli.__name__)
    runpy.run_module(cli.__name__, run_name="__main__")
    assert len(served) == 1
    assert served[0][1:] == ("127.0.0.1", startup_args.port)


@pytest.mark.trace("E07-03", "AGENTS 2")
def test_server_failure_exits_without_exposing_internal_details(
    monkeypatch: pytest.MonkeyPatch,
    startup_args: argparse.Namespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def serve(app: FastAPI, *, host: str, port: int) -> None:
        raise OSError("private runtime detail")

    monkeypatch.setattr(uvicorn, "run", serve)
    monkeypatch.setattr(sys, "argv", arguments(startup_args))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == "ADAPTER_START_OR_RUNTIME_FAILED"
    assert exc.value.__suppress_context__
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
