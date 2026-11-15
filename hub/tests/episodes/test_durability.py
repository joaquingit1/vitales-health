from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from crew_health.kernel.errors import StorageUnavailable

from .builders import station


@pytest.mark.trace("T-E15-027", "V35", "R25")
def test_hard_process_exit_rolls_back_change_and_keeps_accepted_record(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    revision = s.act(ep, {"action": "add_followup", "action_text": "accepted-token"}, key="cmd_durable")
    script = """import os,sqlite3,sys
connection=sqlite3.connect(sys.argv[1]);connection.execute("BEGIN IMMEDIATE")
connection.execute("INSERT INTO episodes_change VALUES (?,?,?,?,?,?,?,?,?)",(sys.argv[2],int(sys.argv[3])+1,'incomplete','engine','engine_one','2026-11-13T12:00:00Z','followup','open',None))
connection.execute("UPDATE episodes_episode SET revision=revision+1,state='open' WHERE episode_id=?",(sys.argv[2],))
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(s.db.path), ep, str(revision)],
        timeout=5,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23, result.stderr
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).revision == revision
        assert len(s.queries.tasks(view, ep)) == 1
        assert view.connection.rows(
            "SELECT count(*) FROM episodes_change WHERE kind=?", ("incomplete",), limit=1
        ) == ((0,),)
    assert (
        s.act(ep, {"action": "add_followup", "action_text": "accepted-token"}, key="cmd_durable", expected=1)
        == revision
    )


@pytest.mark.trace("T-E15-027", "R25")
def test_real_disk_capacity_failure_leaves_no_partial_task_or_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    original = s.db._connect

    def limited() -> sqlite3.Connection:
        connection = original()
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count={pages}")
        return connection

    monkeypatch.setattr(s.db, "_connect", limited)
    failed = False
    for index in range(s.service.limits.max_open_tasks):
        with s.db.snapshot() as view:
            before = s.service.repo.require(view, ep).revision
            count = len(s.queries.tasks(view, ep))
        try:
            s.act(ep, {"action": "add_followup", "action_text": "x" * 500}, key="cmd_full_" + str(index))
        except StorageUnavailable as error:
            assert error.cause == "disk_full"
            failed = True
            with s.db.snapshot() as view:
                assert s.service.repo.require(view, ep).revision == before
                assert len(s.queries.tasks(view, ep)) == count
            break
    assert failed, "fixture did not reach SQLite capacity bound"
    monkeypatch.setattr(s.db, "_connect", original)
