"""Undelivered attempts on disk (EvidenceStore): one JSON file per acquisition in the worker's state directory.

A file is written to a temporary name, flushed to disk and then renamed over its final name, so a crash leaves either
the old file or the new one (os.replace is atomic on Windows, macOS and Linux). An accepted delivery removes it; a
rejected one renames it to `.rejected.json`, kept for an operator and never loaded again. A crash mid write leaves only
the temporary file, which is never loaded. A final file that cannot be read, or more files than the table holds, is
storage damage: loading raises EvidenceUnreadable and the worker refuses to start (AGENTS 6: a storage error fails
loudly), so evidence is never dropped by skipping it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

from crew_health_camera.application.ports import EvidenceUnreadable

_LOG = logging.getLogger("crew_health_camera.evidence")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class FileEvidenceStore:
    def __init__(self, directory: Path, *, max_files: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._dir, self._max = directory, max_files

    def _path(self, acquisition_id: str, suffix: str = ".json") -> Path:
        if _NAME.fullmatch(acquisition_id) is None:
            raise ValueError("acquisition id")
        return self._dir / (acquisition_id.replace(":", "_") + suffix)

    def save(self, acquisition_id: str, document: Mapping[str, object]) -> None:
        target = self._path(acquisition_id)
        temporary = self._path(acquisition_id, ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(document), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def delivered(self, acquisition_id: str) -> None:
        self._path(acquisition_id).unlink(missing_ok=True)

    def rejected(self, acquisition_id: str) -> None:
        path = self._path(acquisition_id)
        if path.exists():
            os.replace(path, self._path(acquisition_id, ".rejected.json"))

    def pending(self) -> tuple[Mapping[str, object], ...]:
        names = sorted(p for p in self._dir.glob("*.json") if not p.name.endswith(".rejected.json"))
        if len(names) > self._max:
            _LOG.error("undelivered attempts exceed max_acquisitions: %d", len(names))
            raise EvidenceUnreadable("more undelivered attempts than the table holds")
        found: list[Mapping[str, object]] = []
        for path in names:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                _LOG.error("undelivered attempt unreadable: %s %s", path.name, type(error).__name__)
                raise EvidenceUnreadable(path.name) from error
            if not isinstance(value, dict):
                raise EvidenceUnreadable(path.name)
            found.append(value)
        return tuple(found)
