"""Refresh the pinned hashes of the product package and the contract copies.

Usage:
    python scripts/refresh_package_hashes.py            rewrite stale entries
    python scripts/refresh_package_hashes.py --check    report stale entries, exit 1 if any
    python scripts/refresh_package_hashes.py --add docs/product/nasa-data/new.csv ...

docs/product/package-manifest.json pins bytes and sha256 of every package file, and
contracts/SHA256SUMS pins sha256 of every file under contracts/api and contracts/reference,
and contracts/decision-overlay/manifest.json binds the overlay to the sha256 of SHA256SUMS.
The script recomputes both for the entries already listed, adds the docs/product paths given
with --add, and leaves alone a listed file this checkout does not have (the manifest also
inventories rendered and research files that are not committed). It never touches generated
output: after a contract file changes, regenerate with
    uv run --project hub python -m crew_health.contracts_gen.build
    uv run --project hub python -m crew_health.contracts_gen.build --typescript
Standard library only; paths are built with pathlib, so it runs on Windows, macOS and Linux.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "docs" / "product"
MANIFEST = PACKAGE / "package-manifest.json"
CONTRACTS = ROOT / "contracts"
SUMS = CONTRACTS / "SHA256SUMS"
OVERLAY = CONTRACTS / "decision-overlay" / "manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_manifest(added: list[str]) -> tuple[str, list[str], list[str]]:
    """Return the new manifest text, the stale or added paths, and the missing paths."""
    raw = MANIFEST.read_text(encoding="utf-8")
    data = json.loads(raw)
    entries = {entry["path"]: entry for entry in data["files"]}
    for name in added:
        if name not in entries:
            entries[name] = {"path": name, "bytes": -1, "sha256": ""}
    changed: list[str] = []
    missing: list[str] = []
    for name, entry in sorted(entries.items()):
        path = PACKAGE / name
        if not path.is_file():
            missing.append(name)
            continue
        size, digest = path.stat().st_size, sha256(path)
        if entry.get("bytes") != size or entry.get("sha256") != digest:
            entry.update({"bytes": size, "sha256": digest})
            changed.append(name)
    data["files"] = [entries[name] for name in sorted(entries)]
    text = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")
    return text, changed, missing


def refresh_sums() -> tuple[str, list[str], list[str]]:
    """Return the new SHA256SUMS text, the stale paths, and the missing paths."""
    lines: list[str] = []
    changed: list[str] = []
    missing: list[str] = []
    for line in SUMS.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        path = CONTRACTS / name
        if not path.is_file():
            missing.append(name)
            lines.append(line)
            continue
        current = sha256(path)
        if current != digest:
            changed.append(name)
        lines.append(current + "  " + name)
    return "\n".join(lines) + "\n", changed, missing


def rebind_overlay(sums_text: str) -> tuple[str, bool] | None:
    """Return the overlay manifest text bound to sums_text and whether it changed."""
    if not OVERLAY.is_file():
        return None
    raw = OVERLAY.read_text(encoding="utf-8")
    data = json.loads(raw)
    bound = hashlib.sha256(sums_text.encode("utf-8")).hexdigest()
    changed = data.get("base_inventory_sha256") != bound
    data["base_inventory_sha256"] = bound
    return json.dumps(data, indent=2) + ("\n" if raw.endswith("\n") else ""), changed


def package_name(argument: str) -> str:
    path = (ROOT / argument).resolve()
    try:
        return path.relative_to(PACKAGE.resolve()).as_posix()
    except ValueError:
        raise SystemExit("not under docs/product: " + argument) from None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="report stale entries without writing")
    parser.add_argument("--add", nargs="+", default=[], metavar="PATH", help="docs/product files to add")
    args = parser.parse_args()
    manifest_text, manifest_changed, manifest_missing = refresh_manifest([package_name(a) for a in args.add])
    sums_text, sums_changed, sums_missing = refresh_sums()
    overlay = rebind_overlay(sums_text)
    for name in manifest_changed:
        print("package-manifest.json: " + name)
    for name in sums_changed:
        print("SHA256SUMS: " + name)
    if overlay is not None and overlay[1]:
        print("decision-overlay/manifest.json: base_inventory_sha256")
    for name in sums_missing:
        print("SHA256SUMS lists a missing file: " + name, file=sys.stderr)
    if manifest_missing:
        print(f"{len(manifest_missing)} manifest entries are not in this checkout; left as they are")
    if args.check:
        stale = manifest_changed or sums_changed or (overlay is not None and overlay[1])
        return 1 if stale or sums_missing else 0
    MANIFEST.write_text(manifest_text, encoding="utf-8", newline="\n")
    SUMS.write_text(sums_text, encoding="utf-8", newline="\n")
    if overlay is not None:
        OVERLAY.write_text(overlay[0], encoding="utf-8", newline="\n")
    return 1 if sums_missing else 0


if __name__ == "__main__":
    sys.exit(main())
