from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from crew_health_bench.adapters.documents import load_document
from crew_health_bench.domain.documents import object_map, text
from crew_health_bench.domain.errors import BenchError


def official_source(root: Path, *, max_file_bytes: int) -> dict[str, object]:
    return load_document(root / "SOURCE.json", max_file_bytes=max_file_bytes,
                         schema_path=root / "SOURCE.schema.json")


def integrity(plan: Mapping[str, object], source: Mapping[str, object], module_sha256: str) -> str:
    expected = object_map(source["harness"])
    if module_sha256 != expected["module_sha256"]:
        return "modified"
    return "official" if all(plan[key] == source[key] for key in ("harness", "datasets", "grader")) else "modified"


def verify_modules(source: Mapping[str, object], installed: Path, *, max_file_bytes: int) -> None:
    files = object_map(source["files"])
    for name in ("healthbench_eval.py", "common.py", "types.py", "sampler/chat_completion_sampler.py"):
        path = installed / name
        try:
            with path.open("rb") as stream:
                raw = stream.read(max_file_bytes + 1)
        except OSError as error:
            raise BenchError("release_unreadable") from error
        if len(raw) > max_file_bytes or sha256(raw).hexdigest() != text(files[name]):
            raise BenchError("official_harness_sha256")
