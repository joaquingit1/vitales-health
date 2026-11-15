"""T-E01-03/T-E01-17 R25: abrupt process exit through production SQLite and ledger."""

import subprocess
import sys
from pathlib import Path

import pytest

# isort: split
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-03", "T-E01-17", "R25")]

_CAPTURE = r"""
import os
import sys
from pathlib import Path
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import command_identity, StoredResult
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, ReplicationChange
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.testing.sqlite import database

db = database(Path(sys.argv[1]), initialize=False)
db.register_audit_policy(AuditPolicy("test.capture",{},frozenset({"fact"}),{"fact":frozenset({"test_record"})}))
identity = command_identity(
    key="command_recovery", actor_ref="operator_1", operation="recordObservation",
    target="/v1/observations", parameters_json=b"{}", body_fingerprint="0"*64, fingerprint_kind="sha256",
)
ledger = CommandLedger(db.ids)
with db.begin(actor=Actor("operator","operator_1"),command=identity,correlation_id="correlation_1") as uow:
    previous = ledger.lookup(uow,identity)
    if previous is None:
        uow.connection.execute("INSERT INTO platform_recovery_probe VALUES(?)",("fact_1",))
        receipt = ledger.accepted(uow,identity)
        ledger.record(uow,identity,StoredResult(receipt,201,"fact_1","accepted",None))
        uow.replicate(ReplicationChange(None,"test_record","fact_1","append",b"{}",(),uow.release.release_id,Audience("operator","fact_1",AudienceProjectionKey("tests","record",1),"1")))
        uow.audit(AuditEntry(db.ids.new("audit"),"test.capture",1,uow.actor,{"fact":"fact_1"},uow.correlation_id,None,{}))
        if sys.argv[2] == "before_commit":
            os._exit(91)
    else:
        receipt = previous.receipt
if sys.argv[2] == "after_commit":
    os._exit(92)
print(receipt.receipt_id)
"""


@pytest.mark.parametrize(
    ("phase", "exit_code", "committed"), [("before_commit", 91, 0), ("after_commit", 92, 1)]
)
def test_process_exit_never_loses_acknowledged_command_or_duplicates_retry(
    tmp_path: Path, phase: str, exit_code: int, committed: int
) -> None:
    db = database(tmp_path)
    db.migrate("CREATE TABLE platform_recovery_probe(id TEXT PRIMARY KEY) STRICT;")
    interrupted = subprocess.run(
        [sys.executable, "-c", _CAPTURE, str(tmp_path), phase],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert interrupted.returncode == exit_code, interrupted.stderr
    with db.snapshot() as view:
        for table in ("platform_recovery_probe", "platform_command_ledger", "platform_audit"):
            assert view.connection.rows("SELECT count(*) FROM " + table, (), limit=1) == ((committed,),)
    retried = subprocess.run(
        [sys.executable, "-c", _CAPTURE, str(tmp_path), "retry"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert retried.returncode == 0, retried.stderr
    repeated = subprocess.run(
        [sys.executable, "-c", _CAPTURE, str(tmp_path), "retry"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert retried.stdout == repeated.stdout
    with db.snapshot() as view:
        for table in ("platform_recovery_probe", "platform_command_ledger", "platform_audit"):
            assert view.connection.rows("SELECT count(*) FROM " + table, (), limit=1) == ((1,),)
