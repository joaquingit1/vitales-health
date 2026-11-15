"""The contained parser process: one request on stdin, one JSON answer on stdout, then exit (PLAN P6, interim).

Started by `ContainedParser` with `python -I`, an empty environment and a timeout. It imports only the standard
library parsing module, reads no path and opens no socket. Input: one header line of JSON, then the file bytes."""

from __future__ import annotations

import json
import sys

from crew_health.reports.adapters import parsing


def _limits(raw: dict[str, object]) -> parsing.ParseLimits:
    return parsing.ParseLimits(
        int(str(raw["max_pages"])),
        int(str(raw["max_pixels"])),
        int(str(raw["max_text_chars"])),
        int(str(raw["max_decoded_bytes"])),
        int(str(raw["max_objects"])),
    )


def handle(header: dict[str, object], data: bytes) -> dict[str, object]:
    limits = _limits(header["limits"])  # type: ignore[arg-type]
    kind = str(header["kind"])
    result = parsing.probe(data, kind, limits)
    if not result.ok:
        return {"ok": False, "reason": result.reason}
    if header["op"] == "inspect" or kind != "pdf":
        return {"ok": True, "pages": [], "proposals": []}
    pages = parsing.pdf_pages(data, limits)
    if pages is None:
        return {"ok": False, "reason": "corrupt"}
    vocabulary = header.get("vocabulary")
    proposals = parsing.propose(
        pages, vocabulary if isinstance(vocabulary, dict) else {}, int(str(header["max_proposals"]))
    )
    return {
        "ok": True,
        "pages": [len(p) for p in pages],
        "proposals": [[p.page, p.start, p.end, p.code, p.value, p.unit] for p in proposals],
    }


def main() -> None:
    raw = sys.stdin.buffer.read()
    line, _, data = raw.partition(b"\n")
    answer = handle(json.loads(line), data)
    sys.stdout.write(json.dumps(answer, separators=(",", ":")))


if __name__ == "__main__":
    main()
