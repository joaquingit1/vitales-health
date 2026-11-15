from __future__ import annotations

from pathlib import Path

from crew_health_bench.adapters.documents import BenchSettings, load_document


def scan_bound(repository: Path, *, max_file_bytes: int) -> int:
    document = load_document(repository / "eval/runner/config/runner-settings.json",
                             schema_path=repository / "eval/runner/schemas/runner-settings.schema.json",
                             max_file_bytes=max_file_bytes)
    return BenchSettings(document).integer("runner.max_files")
