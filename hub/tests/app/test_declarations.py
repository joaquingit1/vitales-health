"""Module registration hooks for storage (SYSTEM 6 and 5.7, 5.8): feature migrations by convention under the
manifest digest, audit policies and replicated tables as DeclarationRegistry declarations."""

import hashlib
from pathlib import Path

import pytest

from crew_health.app.import_contracts import module_digest, module_migrations
from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.replication import ProjectionKey, ReplicatedTable

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.6", "SYSTEM.5.7", "SYSTEM.5.8")]
KEY = ProjectionKey("help", "broadcast", 1)


def registry() -> DeclarationRegistry:
    return DeclarationRegistry(max_ports=8, max_hooks=8)


def test_audit_policy_is_declared_once_per_event_type() -> None:
    declarations = registry()
    policy = AuditPolicy("help.probe", {"outcome": frozenset({"ok"})}, frozenset(), {})
    declarations.audit_policy(policy)
    assert declarations.declarations["audit_policies"] == {"help.probe": policy}
    with pytest.raises(ValueError):
        declarations.audit_policy(AuditPolicy("help.probe", {}, frozenset(), {}))


@pytest.mark.parametrize(
    "table",
    [
        ReplicatedTable("help broadcast", "help_broadcast", "broadcast_id"),
        ReplicatedTable("help_broadcast", "", "broadcast_id"),
        ReplicatedTable("help_broadcast", "help_broadcast", "id;"),
        ReplicatedTable(
            "help_broadcast", "help_broadcast", "broadcast_id", allowed_fields=frozenset({"/state"})
        ),
        ReplicatedTable("help_broadcast", "help_broadcast", "broadcast_id", projection=KEY),
        ReplicatedTable("help_broadcast", "help_broadcast", "broadcast_id", KEY, frozenset({"state"})),
        ReplicatedTable("help_broadcast", "help_broadcast", "broadcast_id", KEY, frozenset({"/"})),
    ],
)
def test_replicated_table_rejects_bad_identity_or_an_allowlist_without_projection(
    table: ReplicatedTable,
) -> None:
    with pytest.raises(ValueError):
        registry().replicated_table(table)


def test_replicated_table_is_declared_once_with_its_allowlist() -> None:
    declarations = registry()
    table = ReplicatedTable("help_broadcast", "help_broadcast", "broadcast_id", KEY, frozenset({"/state"}))
    declarations.replicated_table(table)
    declarations.replicated_table(ReplicatedTable("help_session", "help_session", "help_session_id"))
    assert declarations.declarations["replicated_tables"]["help_broadcast"] == table
    with pytest.raises(ValueError):
        declarations.replicated_table(table)


def module(tmp_path: Path, *names: str) -> Path:
    directory = tmp_path / "probe"
    (directory / "adapters/migrations").mkdir(parents=True)
    (directory / "module.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")
    for name in names:
        (directory / "adapters/migrations" / name).write_text(
            "CREATE TABLE " + name[:4] + "(x);", encoding="utf-8"
        )
    return directory


def test_module_migrations_follow_the_numbered_convention(tmp_path: Path) -> None:
    directory = module(tmp_path, "0002_probe.sql", "0001_probe.sql")
    assert [path.name for path in module_migrations(directory)] == ["0001_probe.sql", "0002_probe.sql"]
    assert module_migrations(tmp_path / "absent") == ()


@pytest.mark.parametrize(
    "names",
    [
        ("0002_probe.sql",),
        ("0001_probe.sql", "0003_probe.sql"),
        ("0001_other.sql",),
        ("0001_probe.sql", "notes.md"),
    ],
)
def test_module_migrations_reject_a_gap_or_a_foreign_file(tmp_path: Path, names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        module_migrations(module(tmp_path, *names))


def test_module_digest_covers_every_migration(tmp_path: Path) -> None:
    directory = module(tmp_path)
    plain = hashlib.sha256((directory / "module.py").read_bytes()).hexdigest()
    assert module_digest(directory) == plain
    migration = directory / "adapters/migrations/0001_probe.sql"
    migration.write_text("CREATE TABLE probe(x);", encoding="utf-8")
    first = module_digest(directory)
    migration.write_text("CREATE TABLE probe(y);", encoding="utf-8")
    assert len({plain, first, module_digest(directory)}) == 3
