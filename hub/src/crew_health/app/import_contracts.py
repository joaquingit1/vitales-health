"""SYSTEM 5/6 convention discovery and static architecture checks."""

from __future__ import annotations

import argparse
import ast
import hashlib
import sys
from pathlib import Path

PURE = frozenset(
    {
        "__future__",
        "abc",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "hashlib",
        "hmac",
        "json",
        "math",
        "re",
        "typing",
    }
)


def discover(root: Path, *, max_modules: int) -> dict[str, str]:
    if not 1 <= max_modules <= 128:
        raise ValueError("registry.max_modules outside E01 11")
    result: dict[str, str] = {}
    for path in sorted(root.glob("*/module.py")):
        name = path.parent.name
        if not name.isidentifier() or not (path.parent / "__init__.py").is_file():
            raise ValueError("invalid first-party module")
        if len(result) >= max_modules:
            raise ValueError("registry.max_modules exceeded")
        result[f"crew_health.{name}.module"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify_modules(root: Path, expected: dict[str, str], *, max_modules: int) -> None:
    if discover(root, max_modules=max_modules) != expected:
        raise ValueError("installed modules differ from build inventory")


def _imports(path: Path, root: Path, *, pure: bool = True) -> tuple[str, ...]:
    source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if pure:
        _check_pure_access(source)
    names: list[str] = []
    relative = path.relative_to(root).with_suffix("").parts
    package = ("crew_health", *relative[:-1])
    for node in ast.walk(source):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in ({"eval", "exec", "__import__", "open"} if pure else {"eval", "exec", "__import__"})
        ):
            raise ValueError("dynamic import or I/O helper: " + str(path))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in (
                {"import_module", "open", "read_text", "read_bytes", "write_text", "write_bytes"}
                if pure
                else {"import_module"}
            )
        ):
            raise ValueError("dynamic import or I/O helper: " + str(path))
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        if isinstance(node, ast.ImportFrom):
            prefix = ".".join(package[: len(package) + 1 - node.level]) + "." if node.level else ""
            module = prefix + (node.module or "")
            if module.endswith(".application"):
                names.extend(module + "." + alias.name for alias in node.names)
            else:
                names.append(module)
            if module in {"crew_health.kernel", "crew_health.contracts_gen"}:
                names.extend(module + "." + alias.name for alias in node.names)
    return tuple(names)


def _check_pure_access(source: ast.Module) -> None:
    aliases: dict[str, str] = {}
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name] = imported.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            for imported in node.names:
                aliases[imported.asname or imported.name] = (node.module or "") + "." + imported.name
    forbidden = {
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "json.load",
        "json.dump",
        "hashlib.file_digest",
    }
    if any(target in forbidden for target in aliases.values()):
        raise ValueError("I/O helper in pure import")
    for node in ast.walk(source):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id
            in {
                "print",
                "input",
                "breakpoint",
                "__import__",
                "eval",
                "exec",
            }
        ):
            raise ValueError("I/O or dynamic execution in pure code")
        if not isinstance(node, ast.Attribute):
            continue
        parts = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            target = aliases.get(current.id, current.id) + "." + ".".join(reversed(parts))
            if target in forbidden:
                raise ValueError("I/O through reviewed standard-library terminal")


def check_kernel(root: Path) -> None:
    paths = sorted((root / "kernel").rglob("*.py"))
    paths.extend(
        p
        for p in (root / "__init__.py", root / "contracts_gen/__init__.py", root / "contracts_gen/enums.py")
        if p.is_file()
    )
    for path in paths:
        for name in _imports(path, root):
            if name in PURE or name == "crew_health.contracts_gen.enums":
                continue
            if name in {"crew_health", "crew_health.kernel", "crew_health.contracts_gen"}:
                continue
            if name.startswith("crew_health.kernel."):
                target = root.joinpath(*name.split(".")[1:]).with_suffix(".py")
                if target.is_file():
                    continue
            raise ValueError("kernel import outside SYSTEM 5 allowlist: " + name)


def check_features(root: Path, modules: tuple[str, ...]) -> None:
    for module in modules:
        if (root / module / "architecture.toml").exists():
            raise ValueError("architecture.toml contributor format needs coordinator binding")
        for path in sorted((root / module).rglob("*.py")):
            parts = path.relative_to(root).parts
            layer = parts[1] if len(parts) > 2 else ""
            imports = _imports(path, root, pure=layer == "domain")
            for target in imports:
                _feature_edge(module, layer, target)
            if path.name == "module.py":
                _check_registration_source(path)


def _feature_edge(module: str, layer: str, target: str) -> None:
    parts = target.split(".")
    if module == "pathways" and parts[0] in {"httpx", "bleak", "cv2"}:
        raise ValueError("pathways-no-io-clients")
    if module == "rules" and ("vehicle" in parts or ("habitat" in parts and "adapters" in parts)):
        raise ValueError("rules-no-vehicle-write")
    if parts[0] == "pharmacy" and (module != "medicines" or layer != "adapters"):
        raise ValueError("pharmacy-only-from-medicines")
    if layer == "domain":
        local = target == f"crew_health.{module}.domain" or target.startswith(f"crew_health.{module}.domain.")
        kernel = target == "crew_health.kernel" or target.startswith("crew_health.kernel.")
        standard = parts[0] in sys.stdlib_module_names and parts[0] not in {
            "os",
            "pathlib",
            "time",
            "sqlite3",
            "subprocess",
            "socket",
            "urllib",
            "http",
            "threading",
            "multiprocessing",
            "random",
            "uuid",
            "importlib",
        }
        if not (local or kernel or standard):
            raise ValueError("domain-pure: " + target)
    if parts[0] != "crew_health" or len(parts) < 2 or parts[1] in {module, "kernel", "contracts_gen"}:
        return
    # SYSTEM 5.12: module.py types register(registry: ModuleRegistry) from app/registry.py;
    # SYSTEM 5.5: routers map ProblemError through app/errors.py. app is the root, not a module.
    if (layer, target) in {("", "crew_health.app.registry"), ("api", "crew_health.app.errors")}:
        return
    if parts[-1] == "module":
        raise ValueError("app-only-assembles")
    if len(parts) < 4 or parts[2:4] != ["application", "ports"]:
        raise ValueError("modules-independent: " + target)
    if parts[1] == "groundlink" and module not in {"rules", "catchup", "mission", "groundlink"}:
        raise ValueError("ground-link-readers")
    safety = {
        "rules",
        "pathways",
        "episodes",
        "help",
        "plans",
        "teach",
        "roles",
        "medicines",
        "routine",
        "guidance",
    }
    if module in safety and parts[1] in {"assistant", "tools"}:
        raise ValueError("no-model-in-safety")


def _check_registration_source(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(
            node, ast.Import | ast.ImportFrom | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        values = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        if values:
            raise ValueError("module import has executable side effects")


def generate_static_imports(root: Path, *, max_modules: int) -> str:
    modules = discover(root, max_modules=max_modules)
    check_features(root, tuple(name.split(".")[1] for name in modules))
    lines = ["# Generated for the application assembly namespace.\n"]
    for index, module in enumerate(modules):
        lines.append(f"from {module} import register as register_{index}\n")
    names = ", ".join(f"register_{index}" for index in range(len(modules)))
    lines.append("REGISTRATIONS = (" + names + ("," if names else "") + ")\n")
    return "".join(lines)


def generate_contracts(root: Path, *, max_modules: int) -> str:
    check_kernel(root)
    modules = [n.split(".")[1] for n in discover(root, max_modules=max_modules)]
    check_features(root, tuple(modules))
    lines = ["[importlinter]", "root_package = crew_health", "include_external_packages = True", ""]
    count = 0

    def contract(name: str, kind: str, body: list[str]) -> None:
        nonlocal count
        count += 1
        lines.extend([f"[importlinter:contract:{count}]", f"name = {name}", f"type = {kind}", *body, ""])

    # Import-linter checks installed dependencies; the AST gate checks every source including TYPE_CHECKING.
    contract(
        "kernel-pure",
        "forbidden",
        [
            "source_modules = crew_health.kernel",
            "forbidden_modules =",
            "    sqlite3",
            "    fastapi",
            "    pydantic",
            "    os",
            "    pathlib",
            "    time",
            "    subprocess",
        ],
    )
    for module in modules:
        base = "crew_health." + module
        layers = [
            layer
            for layer in ("api", "adapters", "application", "domain")
            if (root / module / layer).is_dir()
        ]
        if len(layers) > 1:
            contract(
                module + "-layers", "layers", ["layers =", *("    " + base + "." + layer for layer in layers)]
            )
        else:
            lines.append("# " + module + "-layers: no layer edges in this installed artifact")
        for other in modules:
            if module == other:
                continue
            forbidden = [
                "crew_health." + other + "." + layer
                for layer in ("domain", "adapters", "api")
                if (root / other / layer).is_dir()
            ]
            if forbidden:
                contract(
                    module + "-independent-" + other,
                    "forbidden",
                    [
                        "source_modules = " + base,
                        "forbidden_modules =",
                        *("    " + value for value in forbidden),
                    ],
                )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--imports-output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        from crew_health.contracts_gen.build import load_json, object_map

        manifest = load_json(root / "contracts_gen/manifest.json")
        expected = object_map(manifest["modules"])
        if not all(isinstance(value, str) for value in expected.values()):
            raise ValueError("invalid installed module inventory")
        verify_modules(root, {key: str(value) for key, value in expected.items()}, max_modules=128)
        args.output.write_text(generate_contracts(root, max_modules=128))
        if args.imports_output is not None:
            args.imports_output.write_text(generate_static_imports(root, max_modules=128))
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
