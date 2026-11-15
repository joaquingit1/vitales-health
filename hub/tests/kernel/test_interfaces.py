"""T-E01-01 R01/R06; T-E01-02 SYSTEM 5/6 declaration and discovery invariants."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

from crew_health.app.import_contracts import check_kernel, discover, generate_contracts, verify_modules
from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel import ids

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "T-E01-02", "R01", "R06")]
ROOT = Path(__file__).resolve().parents[3]


def declarations() -> dict[str, list[ast.ClassDef | ast.FunctionDef | ast.TypeAlias]]:
    result: dict[str, list[str]] = {}
    for block in re.findall(r"```python\n(.*?)```", (ROOT / "docs/SYSTEM.md").read_text(), re.S):
        current = ""
        for line in block.splitlines():
            match = re.match(r"# crew_health/(kernel/\w+|app/registry)\.py", line)
            if match:
                current = match[1]
                result.setdefault(current, [])
            if line.startswith("# crew_health/app/errors.py") or line.startswith("# each crew_health/"):
                current = ""
            if current:
                result[current].append(line)
    return {
        name: [
            n
            for n in ast.parse("\n".join(lines)).body
            if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.TypeAlias)
        ]
        for name, lines in result.items()
    }


def test_complete_system_inventory_matches_runtime() -> None:
    for file, nodes in declarations().items():
        module = importlib.import_module("crew_health." + file.replace("/", "."))
        for node in nodes:
            name = node.name.id if isinstance(node, ast.TypeAlias) else node.name
            actual = getattr(module, name)
            if isinstance(node, ast.TypeAlias):
                assert actual.__name__ == name
                continue
            functions = (
                [node]
                if isinstance(node, ast.FunctionDef)
                else [n for n in node.body if isinstance(n, ast.FunctionDef)]
            )
            for fn in functions:
                member = getattr(actual, fn.name) if isinstance(node, ast.ClassDef) else actual
                if isinstance(member, property):
                    member = member.fget
                signature = inspect.signature(member)
                expected = (
                    fn.args.posonlyargs
                    + fn.args.args
                    + ([fn.args.vararg] if fn.args.vararg else [])
                    + fn.args.kwonlyargs
                    + ([fn.args.kwarg] if fn.args.kwarg else [])
                )
                assert list(signature.parameters) == [a.arg for a in expected], (file, name, fn.name)
                for arg in expected:
                    assert (
                        str(signature.parameters[arg.arg].annotation) == ast.unparse(arg.annotation)
                        if arg.annotation
                        else signature.parameters[arg.arg].annotation is inspect.Parameter.empty
                    )
                assert fn.returns is not None
                assert str(signature.return_annotation) == ast.unparse(fn.returns)
            if isinstance(node, ast.ClassDef):
                fields = {
                    n.target.id: ast.unparse(n.annotation)
                    for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                }
                annotations = inspect.get_annotations(actual)
                for field, annotation in fields.items():
                    assert annotations[field] == annotation, (file, name, field)
    assert ids.require_id("record.1") == "record.1"


def test_kernel_import_closure() -> None:
    check_kernel(ROOT / "hub/src/crew_health")


@pytest.mark.parametrize(
    "source",
    [
        "import sqlite3",
        "from pathlib import Path",
        "if TYPE_CHECKING:\n import os",
        "__import__('os')",
        "import importlib",
        "from crew_health.contracts_gen import models",
    ],
)
def test_forbidden_kernel_imports(tmp_path: Path, source: str) -> None:
    (tmp_path / "kernel").mkdir()
    (tmp_path / "kernel/ids.py").write_text(source)
    with pytest.raises(ValueError):
        check_kernel(tmp_path)


def test_transitive_framework_import(tmp_path: Path) -> None:
    (tmp_path / "kernel").mkdir()
    (tmp_path / "contracts_gen").mkdir()
    (tmp_path / "kernel/ids.py").write_text("from crew_health.contracts_gen.enums import ErrorCode")
    (tmp_path / "contracts_gen/enums.py").write_text("from fastapi import FastAPI")
    with pytest.raises(ValueError):
        check_kernel(tmp_path)


def test_discovery_and_manifest_are_exact(tmp_path: Path) -> None:
    module = tmp_path / "new_feature"
    module.mkdir()
    (module / "__init__.py").write_text("")
    (module / "module.py").write_text("def register(registry):\n    pass\n")
    inventory = discover(tmp_path, max_modules=2)
    assert tuple(inventory) == ("crew_health.new_feature.module",)
    verify_modules(tmp_path, inventory, max_modules=2)
    rules = generate_contracts(tmp_path, max_modules=2)
    assert "new_feature-layers" in rules
    assert "new_feature" in rules
    (module / "module.py").write_text("changed")
    with pytest.raises(ValueError):
        verify_modules(tmp_path, inventory, max_modules=2)
    with pytest.raises(ValueError):
        verify_modules(tmp_path, {}, max_modules=2)


def test_registry_graph_requires_are_enforced() -> None:
    class First:
        pass

    class Second:
        pass

    registry = DeclarationRegistry(max_ports=4, max_hooks=4)
    registry.provide(First, lambda resolver: First())
    registry.provide(Second, lambda resolver: Second(), requires=(First,))
    resolver = registry.resolve()
    assert isinstance(resolver.require(Second), Second)
    with pytest.raises(ValueError):
        registry.provide(First, lambda resolver: First())
    cycle = DeclarationRegistry(max_ports=4, max_hooks=4)
    cycle.provide(First, lambda resolver: First(), requires=(Second,))
    cycle.provide(Second, lambda resolver: Second(), requires=(First,))
    with pytest.raises(ValueError):
        cycle.resolve()
    undeclared = DeclarationRegistry(max_ports=4, max_hooks=4)
    undeclared.provide(First, lambda resolver: First())
    undeclared.provide(Second, lambda resolver: (resolver.require(First), Second())[1])
    with pytest.raises(ValueError):
        undeclared.resolve()


def test_fake_counterparts_cover_every_protocol() -> None:
    from typing import get_protocol_members, is_protocol

    from crew_health.kernel import testing

    counterparts = {
        "IdFactory": "MemoryIds",
        "Clock": "MemoryClock",
        "ClockSegments": "MemoryClocks",
        "ClockCorrections": "MemoryClocks",
        "HlcClock": "MemoryClocks",
        "MissionClock": "MemoryMissionClock",
        "UnitRegistry": "MemoryUnits",
        "WireUnitConverter": "MemoryWireConverter",
        "ReleaseHandle": "MemoryRelease",
        "ConfigReleaseReader": "MemoryConfig",
        "ConfigReader": "MemoryConfig",
        "ReleaseCheck": "MemoryReleaseCheck",
        "ActivationGuard": "MemoryActivationGuard",
        "LoaderCheckRegistry": "MemoryLoaderChecks",
        "TemplateRenderer": "MemoryTemplateRenderer",
        "ReleaseActivation": "MemoryReleaseActivation",
        "StorageSession": "MemoryStorage",
        "UnitOfWork": "MemoryUow",
        "UnitOfWorkFactory": "MemoryUowFactory",
        "ReadSnapshot": "MemoryView",
        "ReadSnapshotFactory": "MemoryUowFactory",
        "DomainEventBus": "MemoryEventBus",
        "AudienceRule": "MemoryAudienceRule",
        "OutboxSubscriptions": "MemorySubscriptions",
        "ProjectionProvider": "MemoryProjectionProvider",
        "ReplicaApplier": "MemoryReplicaApplier",
        "ReplicaExchange": "MemoryReplicaExchange",
        "Ownership": "MemoryOwnership",
        "IdempotencyStore": "MemoryCommands",
        "ResultVisibility": "MemoryCommands",
        "CommandPolicy": "MemoryCommands",
        "CommandReceipts": "MemoryCommands",
        "CommandIntents": "MemoryCommands",
        "CommandResumer": "MemoryCommandResumer",
        "PrivateStore": "MemoryPrivateStore",
        "PrivateRecordCoordinator": "MemoryPrivateCoordinator",
        "PrivateReferenceWriter": "MemoryPrivateWriter",
        "PrivateKeyVault": "MemoryVault",
        "AuditVerifier": "MemoryAuditVerifier",
        "Scheduler": "MemoryScheduler",
        "SchedulerRegistry": "MemoryScheduler",
        "HealthState": "MemoryHealth",
        "TaskQueue": "MemoryTasks",
        "TaskRunner": "MemoryTasks",
        "TaskHandler": "MemoryTaskHandler",
        "JobQueries": "MemoryTasks",
        "JobResultProjector": "MemoryJobProjector",
        "Evaluator": "MemoryEvaluator",
        "EvaluationIsolation": "MemoryEvaluation",
        "HubSettings": "MemorySettings",
    }
    observed = set()
    for file, nodes in declarations().items():
        if not file.startswith("kernel/"):
            continue
        module = importlib.import_module("crew_health." + file.replace("/", "."))
        for node in nodes:
            if not isinstance(node, ast.ClassDef):
                continue
            protocol = getattr(module, node.name)
            if not is_protocol(protocol) or node.name == "DomainEvent":
                continue
            observed.add(node.name)
            fake = getattr(testing, counterparts[node.name])
            for member in get_protocol_members(protocol):
                assert hasattr(fake, member) or member in inspect.get_annotations(fake), (node.name, member)
    assert observed == counterparts.keys()


def test_registry_missing_provider_and_early_lookups() -> None:
    class Port:
        pass

    class Missing:
        pass

    missing = DeclarationRegistry(max_ports=2, max_hooks=2)
    missing.provide(Port, lambda r: Port(), requires=(Missing,))
    with pytest.raises(ValueError):
        missing.resolve()
    early = DeclarationRegistry(max_ports=2, max_hooks=2)
    early.provide(Port, lambda r: (r.optional(Missing), Port())[1])
    with pytest.raises(ValueError):
        early.resolve()
    frozen = DeclarationRegistry(max_ports=2, max_hooks=2)
    with pytest.raises(ValueError):
        frozen.freeze(required_loader_checks=frozenset({"mandatory"}))
    frozen.freeze(required_loader_checks=frozenset())
    with pytest.raises(ValueError):
        frozen.internal_command("late")


def test_discovered_module_contributes_all_registration_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import crew_health

    package = tmp_path / "crew_health"
    feature = package / "temporary_feature"
    feature.mkdir(parents=True)
    (feature / "__init__.py").write_text("")
    for layer in ["domain", "application", "adapters", "api"]:
        (feature / layer).mkdir()
        (feature / layer / "__init__.py").write_text("")
    (feature / "module.py").write_text("""
from decimal import Decimal
from hashlib import sha256
from fastapi import APIRouter
from crew_health.kernel.replication import ProjectionKey, ProjectionRegistration
from crew_health.kernel.tasks import TaskRegistration, TaskResult
from crew_health.kernel.evaluation import EvaluatorRegistration
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.testing import MemoryProjectionProvider, MemoryReplicaApplier, MemoryTaskHandler
from crew_health.kernel.testing import MemoryEvaluator, MemoryPrivateWriter
class Port: pass
def register(registry):
    registry.provide(Port, lambda resolver: Port())
    registry.router(APIRouter())
    registry.projection(ProjectionRegistration(ProjectionKey("temporary_feature", "test", 1),
        b"{}", sha256(b"{}").hexdigest(), MemoryProjectionProvider(lambda *args: None),
        MemoryReplicaApplier(lambda *args: None)))
    registry.task(TaskRegistration("temporary_feature", "v1", MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "result", "hash"),
        lambda *args: None, lambda *args: None), None, None))
    registry.evaluator(EvaluatorRegistration("temporary_feature", "v1", MemoryEvaluator(lambda *args: None)))
    registry.private_records("temporary_feature", MemoryPrivateWriter())
    registry.setting(SettingSpec("temporary_feature.count", "integer", None, Decimal(1), Decimal(2),
        None, "SYSTEM 5.11 test-only bound"))
""")
    inventory = discover(package, max_modules=1)
    assert tuple(inventory) == ("crew_health.temporary_feature.module",)
    monkeypatch.setattr(crew_health, "__path__", [*crew_health.__path__, str(package)])
    try:
        module = importlib.import_module(next(iter(inventory)))
        registry = DeclarationRegistry(max_ports=8, max_hooks=16)
        module.register(registry)
        registry.resolve()
        registry.freeze(required_loader_checks=frozenset())
        assert set(registry.declarations) == {
            "ports",
            "routers",
            "projections",
            "tasks",
            "evaluators",
            "private_writers",
            "settings",
        }
        contracts = generate_contracts(package, max_modules=1)
        assert "temporary_feature-layers" in contracts
        verify_modules(package, inventory, max_modules=1)
        from crew_health.app.import_contracts import generate_static_imports

        static_imports = generate_static_imports(package, max_modules=1)
        source = ast.parse(static_imports)
        assert [n.module for n in source.body if isinstance(n, ast.ImportFrom)] == list(inventory)
    finally:
        import sys

        sys.modules.pop("crew_health.temporary_feature.module", None)
        sys.modules.pop("crew_health.temporary_feature", None)


@pytest.mark.parametrize(
    "path,source",
    [
        ("first/domain/value.py", "import fastapi"),
        ("first/application/value.py", "from crew_health.second.domain import value"),
        ("first/application/value.py", "from crew_health.second.application import service"),
        ("first/application/value.py", "from crew_health.second.module import register"),
        ("first/adapters/value.py", "import pharmacy"),
        ("first/application/value.py", "from crew_health.groundlink.application.ports import Reader"),
        ("rules/application/value.py", "from crew_health.assistant.application.ports import Model"),
        ("first/module.py", "open('side-effect')"),
        ("first/application/value.py", "from crew_health.app.registry import ModuleRegistry"),
        ("first/module.py", "from crew_health.app.assembly import create_app"),
        ("first/application/value.py", "from crew_health.app.errors import to_envelope"),
    ],
)
def test_feature_architecture_rejects_forbidden_ingress(tmp_path: Path, path: str, source: str) -> None:
    for name in ["first", "second", "rules", "assistant", "groundlink"]:
        module = tmp_path / name
        module.mkdir()
        (module / "__init__.py").write_text("")
        (module / "module.py").write_text("def register(registry):\n    pass\n")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    with pytest.raises(ValueError):
        generate_contracts(tmp_path, max_modules=8)


def test_feature_registration_and_router_errors_may_use_the_composition_root(tmp_path: Path) -> None:
    module = tmp_path / "first"
    (module / "api").mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "api/__init__.py").write_text("", encoding="utf-8")
    (module / "module.py").write_text(
        "from crew_health.app.registry import ModuleRegistry\n"
        "def register(registry: ModuleRegistry) -> None:\n    pass\n",
        encoding="utf-8",
    )
    (module / "api/router.py").write_text(
        "from crew_health.app.errors import to_envelope\n", encoding="utf-8"
    )
    assert "first" in generate_contracts(tmp_path, max_modules=8)


def test_runtime_signature_parameter_kinds_and_defaults() -> None:
    for file, nodes in declarations().items():
        module = importlib.import_module("crew_health." + file.replace("/", "."))
        for node in nodes:
            if isinstance(node, ast.TypeAlias):
                continue
            actual = getattr(module, node.name)
            functions = (
                [node]
                if isinstance(node, ast.FunctionDef)
                else [n for n in node.body if isinstance(n, ast.FunctionDef)]
            )
            for fn in functions:
                member = getattr(actual, fn.name) if isinstance(node, ast.ClassDef) else actual
                if isinstance(member, property):
                    member = member.fget
                parameters = inspect.signature(member).parameters
                for arg in fn.args.kwonlyargs:
                    assert parameters[arg.arg].kind == inspect.Parameter.KEYWORD_ONLY
                if fn.args.vararg is not None:
                    assert parameters[fn.args.vararg.arg].kind == inspect.Parameter.VAR_POSITIONAL
                positional = fn.args.posonlyargs + fn.args.args
                expected_defaults = (
                    dict(
                        zip(
                            [a.arg for a in positional[-len(fn.args.defaults) :]],
                            fn.args.defaults,
                            strict=True,
                        )
                    )
                    if fn.args.defaults
                    else {}
                )
                expected_defaults.update(
                    {
                        arg.arg: value
                        for arg, value in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True)
                        if value is not None
                    }
                )
                for key, parameter in parameters.items():
                    if key in expected_defaults:
                        assert parameter.default == ast.literal_eval(expected_defaults[key])
                    else:
                        assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\ndatetime.now()",
        "import datetime as dt\ndt.datetime.utcnow()",
        "from datetime import date as D\nD.today()",
        "print('io')",
        "import json\njson.load(stream)",
    ],
)
def test_kernel_cannot_use_io_through_allowlisted_modules(tmp_path: Path, source: str) -> None:
    (tmp_path / "kernel").mkdir()
    (tmp_path / "kernel/value.py").write_text(source)
    with pytest.raises(ValueError):
        check_kernel(tmp_path)


def test_kernel_runtime_import_is_framework_free() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import crew_health.kernel.testing; "
            "assert not {'pydantic', 'fastapi', 'sqlite3', 'pathlib', 'subprocess', 'threading'} "
            "& set(sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
