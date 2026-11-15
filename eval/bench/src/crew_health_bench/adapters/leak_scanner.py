from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from crew_health.kernel.commands import canonical_json

from crew_health_bench.adapters.files import bounded_files
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.leak import Fingerprint, scan_text


class LeakScanner:
    def __init__(self, *, max_files: int, max_file_bytes: int) -> None:
        if max_files < 1 or max_file_bytes < 1:
            raise BenchError("case_invalid")
        self.max_files, self.maximum = max_files, max_file_bytes

    def _text(self, path: Path) -> str:
        vault_path(path.parent, path.name)
        try:
            with path.open("rb") as stream:
                raw = stream.read(self.maximum + 1)
            if len(raw) > self.maximum:
                raise BenchError("target_unreadable", exit_code=6)
            return raw.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise BenchError("target_unreadable", exit_code=6) from error

    def scan(self, targets: tuple[tuple[str, Path], ...], holdout: tuple[Fingerprint, ...], *,
             fp_salt: str, leak_jaccard: float) -> list[dict[str, object]]:
        if not targets or len(targets) > self.max_files:
            raise BenchError("target_unreadable", exit_code=6)
        findings: list[dict[str, object]] = []
        visited = 0
        total_bytes = 0
        for kind, root in targets:
            if kind not in ("prompts", "tools", "examples", "config", "evidence", "dev"):
                raise BenchError("case_invalid")
            files = bounded_files(root, max_files=self.max_files)
            visited += len(files)
            if visited > self.max_files:
                raise BenchError("target_unreadable", exit_code=6)
            for path in files:
                for finding in scan_text(self._text(path), holdout, fp_salt=fp_salt,
                                         leak_jaccard=leak_jaccard, target=kind):
                    row = {**asdict(finding), "path": str(path)}
                    total_bytes += len(canonical_json(row))
                    if total_bytes > self.maximum:
                        raise BenchError("target_unreadable", exit_code=6)
                    findings.append(row)
        return findings
