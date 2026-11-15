from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from crew_health_bench.adapters.isolation import vault_path


class WindowsFiles:
    def __init__(self, *, move: Callable[[str, str, int], int], last_error: Callable[[], int]) -> None:
        self._move, self._last_error = move, last_error

    def replace(self, source: Path, target: Path) -> None:
        if source.parent != target.parent:
            raise OSError("atomic replacement must stay in one directory")
        status: object = self._move(str(source), str(target), 0x1 | 0x8)
        if type(status) is not int or status == 0:
            raise OSError(self._last_error(), "native write-through move failed")

    def sync_directory(self, directory: Path) -> None:
        temporary = vault_path(directory, ".chc-durable.tmp")
        marker = vault_path(directory, ".chc-durable")
        with temporary.open("wb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        self.replace(temporary, marker)


def load_windows() -> WindowsFiles:
    if sys.platform != "win32" or os.name != "nt":
        raise OSError("native Windows file support is unavailable")
    import ctypes

    library = ctypes.WinDLL("kernel32", use_last_error=True)
    move = library.MoveFileExW
    move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    move.restype = ctypes.c_int
    return WindowsFiles(move=move, last_error=ctypes.get_last_error)
