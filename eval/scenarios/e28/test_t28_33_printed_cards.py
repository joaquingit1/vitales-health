"""Dissimilar backup through a subprocess, with a database-hostile working directory."""

import os
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import cast

import pytest
from stack import ROOT, load_json, obj, rows

pytestmark = pytest.mark.trace("T28-33", "AGENTS-2", "EI1", "EI6")


def documents(tmp_path: Path) -> Path:
    source = tmp_path / "documents"
    source.mkdir()
    for name in ("onboard-plans.json", "kit-map.json"):
        shutil.copyfile(ROOT / "config" / name, source / name)
    shutil.copyfile(ROOT / "docs/product/api/examples/config/ui-copy.example.json", source / "ui-copy.json")
    return source


def cli(source: Path, output: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    # The executable is uv's already selected, pinned hub interpreter. No hub is running.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "crew_health.plans.cli",
            "print-cards",
            "--documents",
            str(source),
            "--environment",
            "lunar_surface",
            "--out",
            str(output),
        ],
        cwd=cwd,
        env={
            **os.environ,
            "UV_CACHE_DIR": "/private/tmp/vitales-e28-uv-cache",
            "CREW_HEALTH_DATA_DIR": "/dev/null/cannot-open-sqlite",
            "DATABASE_URL": "sqlite:////dev/null/cannot-open-sqlite",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_every_row_card_has_release_content_and_two_runs_are_identical(tmp_path: Path) -> None:
    source = documents(tmp_path)
    output = tmp_path / "cards"
    # A conventional SQLite path is a directory, so attempts to open it as a database fail.
    (tmp_path / "hub.sqlite").mkdir()
    result = cli(source, output, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    first = {path.name: path.read_bytes() for path in output.iterdir()}
    raw = load_json(source / "onboard-plans.json")
    kit = load_json(source / "kit-map.json")
    copy = load_json(source / "ui-copy.json")
    plans = rows(raw["plans"])
    assert len(first) == len(plans) + 1
    for row in rows(kit["conditions"]):
        assert "plan_" + str(row["condition_id"]).lower() + ".html" in first
    for plan in plans:
        html = first[str(plan["plan_id"]) + ".html"].decode("utf-8")
        assert escape(str(raw["version"])) in html
        assert escape(str(raw["notice"])) in html
        assert 'data-basis="packed"' in html
        assert "lunar_surface: packed" in html
        positions = []
        for stage in rows(plan["stages"]):
            if stage["stage_id"] == "return" and "lunar_surface" not in cast(
                list[str], stage["environments"]
            ):
                continue
            title = str(obj(copy["plan_stage_words"])[str(stage["stage_id"])])
            positions.append(html.index("<h2>" + escape(title) + "</h2>"))
            for step in rows(stage.get("steps", stage.get("prepare", []))):
                if step.get("environments") is not None and "lunar_surface" not in cast(
                    list[str], step["environments"]
                ):
                    continue
                assert escape(str(step["text"])) in html
                needs = obj(step["needs"])
                for field in ("instruments", "skills"):
                    for need in cast(list[str], needs[field]):
                        assert escape(need) in html
                for supply in rows(needs["supplies"]):
                    assert escape(str(supply["supply_id"])) in html
                if step["substitute_step_id"]:
                    substitute = next(
                        s
                        for st in rows(plan["stages"])
                        for s in rows(st.get("steps", st.get("prepare", [])))
                        if s["step_id"] == step["substitute_step_id"]
                    )
                    assert escape(str(substitute["text"])) in html
        assert positions == sorted(positions)
    again = cli(source, output, tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr
    assert {path.name: path.read_bytes() for path in output.iterdir()} == first
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix in {".db", ".sqlite", ".sqlite3"}]


def test_corrupt_documents_exit_two_and_leave_output_empty(tmp_path: Path) -> None:
    source = documents(tmp_path)
    (source / "onboard-plans.json").write_text("{", encoding="utf-8")
    output = tmp_path / "cards"
    output.mkdir()
    result = cli(source, output, tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert '"CONFIG_INVALID"' in result.stdout
    assert list(output.iterdir()) == []


def test_every_card_preserves_its_configured_source_ids(tmp_path: Path) -> None:
    source = documents(tmp_path)
    output = tmp_path / "cards"
    result = cli(source, output, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    missing = []
    for plan in rows(load_json(source / "onboard-plans.json")["plans"]):
        html = (output / (str(plan["plan_id"]) + ".html")).read_text(encoding="utf-8")
        for stage in rows(plan["stages"]):
            for step in rows(stage.get("steps", stage.get("prepare", []))):
                # A step limited to other environments is not on a lunar_surface card.
                if step.get("environments") is not None and "lunar_surface" not in cast(
                    list[str], step["environments"]
                ):
                    continue
                for source_id in cast(list[str], step["source_ids"]):
                    if escape(source_id) not in html:
                        missing.append((plan["plan_id"], step["step_id"], source_id))
    assert missing == [], missing[:10]
