"""Shared test helpers (foundation-owned; builders may add fixtures in their own
directories' conftest.py files, never here)."""

import hashlib
import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pharmacy.config import ConfigBundle, load_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
VECTOR_DIR = REPO_ROOT / "tests" / "data" / "vectors"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "pharmacy"


def cache_byte_hashes(directory: Path) -> dict[str, str]:
    """Hash all cache file bytes and names, including additions and deletions."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def isolated_xtb_cache(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Redirect test cache writes and detect changes to the committed cache independently."""
    committed = REPO_ROOT / "data" / "pharmacy" / "xtb-cache"
    before = cache_byte_hashes(committed)
    temporary = tmp_path_factory.mktemp("pharmacy-xtb") / "xtb-cache"
    shutil.copytree(committed, temporary)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PHARMACY_XTB_CACHE_DIR", str(temporary))
        try:
            yield temporary
        finally:
            after = cache_byte_hashes(committed)
            changed = sorted(
                name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
            )
            if changed:
                pytest.fail(
                    "Committed xTB cache bytes changed during the test session: " + ", ".join(changed)
                )


def _all_vectors() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(VECTOR_DIR.glob("*.json")):
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
            out[vector["vector_id"]] = vector
    return out


@pytest.fixture(scope="session")
def bundle() -> ConfigBundle:
    """The validated configuration bundle of config/pharmacy."""
    return load_bundle()


@pytest.fixture(scope="session")
def vector() -> Callable[[str], dict[str, Any]]:
    """Look up a test vector by id: ``vector("TV-MKT-2")``."""
    vectors = _all_vectors()

    def get(vector_id: str) -> dict[str, Any]:
        return vectors[vector_id]

    return get


@pytest.fixture(scope="session")
def demo_scenario() -> dict[str, Any]:
    """fixtures/pharmacy/demo-moon-base.json as parsed JSON."""
    data: dict[str, Any] = json.loads((FIXTURE_DIR / "demo-moon-base.json").read_text(encoding="utf-8"))
    return data
