"""A process killed inside the ConfirmDose unit of work (E32 4.4 step 6, E32-T-54, AGENTS 4 "kill the process mid
write"). A child process confirms against the station's database file and dies with os._exit after the
administration insert and before the commit; the parent then restarts on the same file and finds no trace of the
command, a proposal expired by the restart rule (C5) and a retry that succeeds once."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .harness import REPO, Station, StepClock, build_station, headers
from .test_flow import _confirm, _context, _diagnose, _propose

HUB = REPO / "hub"
CHILD_SECONDS = 120  # bound on the child process: imports, station start and one confirm
CRASH_EXIT = 9
ID_SKIP = (
    10_000  # the restarted station's MemoryIds starts at zero again; skip past every ID the first run used
)

CHILD = """
import json
import os
import sys
from pathlib import Path

from crew_health.medicines.adapters.sqlite import SqliteMedicines
from tests.medicines.harness import build_station
from tests.medicines.test_flow import _confirm

directory, point, key, proposal = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])


def crash(*args: object, **kwargs: object) -> None:
    os._exit({exit_code})


setattr(SqliteMedicines, point, crash)
station = build_station(Path(directory), queue=False)
response = _confirm(station, proposal, key=key)
print(response.status_code, response.text)
sys.exit(3)
"""


def _tables(st: Station) -> dict[str, object]:
    names = [str(r[0]) for r in st.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: st.rows(f'SELECT count(*) FROM "{name}"')[0][0] for name in names}


def _crash_inside_confirm(
    directory: Path, script: Path, point: str, key: str, proposal: dict[str, object]
) -> None:
    script.write_text(CHILD.format(exit_code=CRASH_EXIT), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(HUB)}
    child = subprocess.run(
        [sys.executable, str(script), str(directory), point, key, json.dumps(proposal)],
        cwd=HUB,
        env=env,
        capture_output=True,
        text=True,
        timeout=CHILD_SECONDS,
        check=False,
    )
    assert child.returncode == CRASH_EXIT, child.stdout + child.stderr


@pytest.mark.trace("T-54", "INV-M4", "INV-M5", "C5", "AGENTS-6")
@pytest.mark.parametrize(
    "point",
    [
        "add_movement",  # after the administration and its accounting rows, before the movement and the audit
        "add_terminal",  # after the movement and the audit entry: the last write before the commit
    ],
)
def test_a_process_killed_inside_the_confirm_leaves_nothing_and_the_retry_succeeds_once(
    tmp_path: Path, point: str
) -> None:
    directory = tmp_path / "hub"
    directory.mkdir()
    st = build_station(directory, queue=False)  # no task queue: a start-up writes no run rows to compare
    _context(st)
    proposal = _propose(st, _diagnose(st))
    before = _tables(st)
    key = "key-crash-0001"

    _crash_inside_confirm(directory, tmp_path / "child.py", point, key, proposal)

    restarted = build_station(directory, clock=StepClock(epoch="epoch_2"), queue=False)
    for _ in range(ID_SKIP):
        restarted.db.ids.new("skip")
    assert (
        _tables(restarted) == before
    )  # no administration, movement, terminal event, audit, ledger or outbox row
    shown = restarted.client.get(f"/v1/dose-proposals/{proposal['proposal_id']}", headers=headers())
    assert shown.status_code == 200 and shown.json()["state"] == "expired"  # C5: never confirmed half done

    retried = _confirm(restarted, proposal, key=key)
    assert retried.status_code == 409 and retried.json()["current_state"]["state"] == "expired"
    assert restarted.rows("SELECT state FROM medicines_proposal_terminal") == [("expired",)]

    fresh = _propose(restarted, _diagnose(restarted))
    done = _confirm(restarted, fresh, key="key-crash-0002")
    assert done.status_code == 201, done.text
    again = _confirm(restarted, fresh, key="key-crash-0002")
    assert again.status_code == 201 and again.content == done.content
    assert restarted.rows("SELECT count(*) FROM medicines_record WHERE kind='administration'") == [(1,)]
    assert restarted.rows("SELECT count(*) FROM medicines_movement") == [(1,)]
