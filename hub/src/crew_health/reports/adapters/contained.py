"""Runs the parser worker as a separate process with a timeout and a bounded answer (AGENTS 2 contain failures,
PLAN P6 interim). A crash, hang, oversized or malformed answer is `ParserUnavailable`: the report stays
unextracted and the person reviews the source and enters findings by hand. Nothing falls back to guessing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from crew_health.reports.adapters.parsing import ParseLimits


class ParserUnavailable(Exception):
    """The contained parser did not give a usable answer within its bounds."""


@dataclass(frozen=True, slots=True)
class ParserBounds:
    limits: ParseLimits
    timeout_ms: int
    max_output_bytes: int
    max_proposals: int

    def __post_init__(self) -> None:
        if min(self.timeout_ms, self.max_output_bytes, self.max_proposals) < 1:
            raise ValueError("bounds")


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    ok: bool
    reason: str | None
    page_text_lengths: tuple[int, ...]
    proposals: tuple[tuple[int, int, int, str, str, str | None], ...]


class ContainedParser:
    def __init__(self, bounds: ParserBounds) -> None:
        self.bounds = bounds

    def run(self, op: str, kind: str, data: bytes, vocabulary: dict[str, dict[str, object]]) -> ParseOutcome:
        header = json.dumps(
            {
                "op": op,
                "kind": kind,
                "vocabulary": vocabulary,
                "max_proposals": self.bounds.max_proposals,
                "limits": {
                    "max_pages": self.bounds.limits.max_pages,
                    "max_pixels": self.bounds.limits.max_pixels,
                    "max_text_chars": self.bounds.limits.max_text_chars,
                    "max_decoded_bytes": self.bounds.limits.max_decoded_bytes,
                    "max_objects": self.bounds.limits.max_objects,
                },
            },
            separators=(",", ":"),
        ).encode()
        with tempfile.TemporaryDirectory(prefix="crew-health-parse-") as scratch:
            try:
                done = subprocess.run(
                    [sys.executable, "-I", "-m", "crew_health.reports.adapters.worker"],
                    input=header + b"\n" + data,
                    capture_output=True,
                    timeout=self.bounds.timeout_ms / 1000,
                    cwd=scratch,
                    env={},
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as error:
                raise ParserUnavailable("parser_timeout") from error
        if done.returncode != 0 or len(done.stdout) > self.bounds.max_output_bytes:
            raise ParserUnavailable("parser_failed")
        return _outcome(done.stdout)


def _outcome(raw: bytes) -> ParseOutcome:
    try:
        answer = json.loads(raw)
        if answer["ok"] is not True:
            return ParseOutcome(False, str(answer["reason"]), (), ())
        return ParseOutcome(
            True,
            None,
            tuple(int(n) for n in answer["pages"]),
            tuple(
                (int(p[0]), int(p[1]), int(p[2]), str(p[3]), str(p[4]), None if p[5] is None else str(p[5]))
                for p in answer["proposals"]
            ),
        )
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise ParserUnavailable("parser_answer") from error
