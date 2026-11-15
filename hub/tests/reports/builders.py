"""Typed access to the E09 test inputs, which live in eval/scenarios/E09/inputs (AGENTS 4: test data lives only
in eval/). The module is loaded from its file because the directory name is not a package."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_PATH = Path(__file__).resolve().parents[3] / "eval" / "scenarios" / "E09" / "inputs" / "builders.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e09_inputs_builders", _PATH)
    if spec is None or spec.loader is None:
        raise ImportError(str(_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _load()


def pdf(pages: list[list[str]], *, flate: bool = False, extra: bytes = b"") -> bytes:
    data: bytes = _M.pdf(pages, flate=flate, extra=extra)
    return data


def png(width: int = 2, height: int = 2) -> bytes:
    data: bytes = _M.png(width, height)
    return data


def jpeg(width: int = 2, height: int = 2) -> bytes:
    data: bytes = _M.jpeg(width, height)
    return data


def pdf_with_javascript() -> bytes:
    data: bytes = _M.pdf_with_javascript()
    return data


def truncated_pdf() -> bytes:
    data: bytes = _M.truncated_pdf()
    return data


def png_bad_crc() -> bytes:
    data: bytes = _M.png_bad_crc()
    return data


def png_huge_dimensions() -> bytes:
    data: bytes = _M.png_huge_dimensions()
    return data


def pdf_bomb(size: int) -> bytes:
    data: bytes = _M.pdf_bomb(size)
    return data
