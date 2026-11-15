"""T28-33: printed backups need only document files, without a database or hub process."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import cast

import pytest

from crew_health.plans.cli import main

from .plans_stack import ROOT, load_json, obj, rows


def documents(tmp_path: Path) -> Path:
    source = tmp_path / "documents"
    source.mkdir()
    for filename in ("onboard-plans.json", "kit-map.json"):
        shutil.copyfile(ROOT / "config" / filename, source / filename)
    shutil.copyfile(ROOT / "docs/product/api/examples/config/ui-copy.example.json", source / "ui-copy.json")
    return source


@pytest.mark.trace("T28-33", "AGENTS-2", "EI6")
@pytest.mark.parametrize("environment", ["station_leo", "transit", "lunar_surface", "mars_surface"])
def test_cards_print_all_119_conditions_and_flow_backups_deterministically(
    tmp_path: Path, environment: str
) -> None:
    source = documents(tmp_path)
    output = tmp_path / "cards"
    args = ["print-cards", "--documents", str(source), "--environment", environment, "--out", str(output)]
    assert main(args) == 0
    raw = load_json(source / "onboard-plans.json")
    plans = [p for p in rows(raw["plans"]) if environment in cast(list[str], p["environments"])]
    assert len([p for p in plans if "condition_id" in obj(p["scope"])]) == 119
    # The built release additionally contains 20 flow plans. The spec requires a card for every plan.
    assert len(list(output.glob("*.html"))) == len(plans) + 1
    first = {p.name: p.read_bytes() for p in output.iterdir()}
    for plan in plans:
        html = first[str(plan["plan_id"]) + ".html"].decode("utf-8")
        assert escape(str(plan["title"])) in html
        assert escape(str(raw["notice"])) in html
        assert 'data-basis="packed"' in html
        for stage in rows(plan["stages"]):
            if stage["stage_id"] == "return" and environment not in cast(list[str], stage["environments"]):
                continue
            for step in rows(stage.get("steps", stage.get("prepare", []))):
                if step.get("environments") is None or environment in cast(list[str], step["environments"]):
                    assert escape(str(step["text"])) in html, (plan["plan_id"], step["step_id"])
                    if step["cannot_replace_text"]:
                        assert escape(str(step["cannot_replace_text"])) in html
    kit = load_json(source / "kit-map.json")
    index = first["index.html"].decode("utf-8")
    positions = [
        index.index(str(c.get("plan_id") or "plan_" + str(c["condition_id"]).lower()) + ".html")
        for c in rows(kit["conditions"])
    ]
    assert positions == sorted(positions)
    assert main(args) == 0
    assert {p.name: p.read_bytes() for p in output.iterdir()} == first
    assert not list(tmp_path.rglob("*.sqlite*")) and not list(tmp_path.rglob("*.db"))


@pytest.mark.trace("T28-33", "AGENTS-5")
@pytest.mark.parametrize("failure", ["corrupt", "missing", "bad_words", "bad_kit"])
def test_invalid_print_documents_exit_two_before_creating_any_card(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    source = documents(tmp_path)
    if failure == "corrupt":
        (source / "onboard-plans.json").write_text("{", encoding="utf-8")
    elif failure == "missing":
        (source / "kit-map.json").unlink()
    elif failure == "bad_words":
        (source / "ui-copy.json").write_text("{}", encoding="utf-8")
    else:
        (source / "kit-map.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "cards"
    output.mkdir()
    assert (
        main(["print-cards", "--documents", str(source), "--environment", "transit", "--out", str(output)])
        == 2
    )
    assert json.loads(capsys.readouterr().out)["code"] == "CONFIG_INVALID"
    assert list(output.iterdir()) == []


@pytest.mark.trace("T28-33", "EI1")
def test_print_cli_import_is_independent_of_api_platform_and_app() -> None:
    script = (
        "import sys; import crew_health.plans.cli; "
        'assert not any(n.startswith(("crew_health.plans.api", "crew_health.platform", "crew_health.app", "fastapi")) for n in sys.modules)'
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.trace("T28-33", "AGENTS-7")
def test_print_cards_escape_document_markup(tmp_path: Path) -> None:
    source = documents(tmp_path)
    raw = load_json(source / "onboard-plans.json")
    injected = '<script>alert("crew")</script> & <img src=x onerror="alert(1)">'
    raw["notice"] = injected
    rows(raw["plans"])[0]["title"] = injected
    (source / "onboard-plans.json").write_text(json.dumps(raw), encoding="utf-8")
    output = tmp_path / "cards"
    assert (
        main(["print-cards", "--documents", str(source), "--environment", "transit", "--out", str(output)])
        == 0
    )
    html = (output / str(rows(raw["plans"])[0]["plan_id"])).with_suffix(".html").read_text(encoding="utf-8")
    assert injected not in html
    assert escape(injected) in html
    assert "<script>" not in (output / "index.html").read_text(encoding="utf-8")
