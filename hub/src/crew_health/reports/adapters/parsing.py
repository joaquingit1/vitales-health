"""Bounded local parsing of a report file: decode integrity, text layer and proposals (E09-01, E09-02).

Pure standard library and no import from the hub, so the contained worker loads only this file. Every loop,
scan and decompression has a bound passed in `ParseLimits`; nothing here executes, renders or follows
anything inside a file. Text found in a source is inert data. A file that cannot be proven well formed within the
bounds is reported `corrupt`, never guessed at. No OCR and no image interpretation exist here: an image or an
image-only PDF yields no text, and the person reviews the source and enters findings by hand (PLAN design
decisions). Text offsets are Unicode code points of the extracted page text (PLAN P6, interim)."""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_pages: int
    max_pixels: int
    max_text_chars: int
    max_decoded_bytes: int
    max_objects: int

    def __post_init__(self) -> None:
        if (
            min(
                self.max_pages, self.max_pixels, self.max_text_chars, self.max_decoded_bytes, self.max_objects
            )
            < 1
        ):
            raise ValueError("limits")


@dataclass(frozen=True, slots=True)
class Probe:
    ok: bool
    reason: str | None  # "corrupt" or "too_large" when not ok
    pages: int


@dataclass(frozen=True, slots=True)
class Proposal:
    page: int
    start: int
    end: int
    code: str
    value: str
    unit: str | None


def _bad(reason: str = "corrupt") -> Probe:
    return Probe(False, reason, 0)


# --- decode integrity -------------------------------------------------------------------------------------


def _probe_png(data: bytes, limits: ParseLimits) -> Probe:
    if not data.startswith(PNG_MAGIC):
        return _bad()
    pos, first, ended = len(PNG_MAGIC), True, False
    for _ in range(limits.max_objects):
        if pos == len(data):
            break
        if ended or pos + 12 > len(data):
            return _bad()
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            return _bad()
        if zlib.crc32(data[pos + 4 : end]) != struct.unpack(">I", data[end : end + 4])[0]:
            return _bad()
        if first:
            if kind != b"IHDR" or length != 13:
                return _bad()
            width, height = struct.unpack(">II", data[pos + 8 : pos + 16])
            if width < 1 or height < 1:
                return _bad()
            if width * height > limits.max_pixels:
                return _bad("too_large")
            first = False
        ended = kind == b"IEND"
        pos = end + 4
    else:
        return _bad("too_large")
    return Probe(ended and not first, None if ended else "corrupt", 1 if ended else 0)


def _probe_jpeg(data: bytes, limits: ParseLimits) -> Probe:
    if data[:3] != b"\xff\xd8\xff":
        return _bad()
    pos, dims = 2, False
    for _ in range(limits.max_objects):
        if pos + 4 > len(data) or data[pos] != 0xFF:
            return _bad()
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if length < 2 or pos + 2 + length > len(data):
            return _bad()
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if length < 8:
                return _bad()
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            if width < 1 or height < 1:
                return _bad()
            if width * height > limits.max_pixels:
                return _bad("too_large")
            dims = True
        pos += 2 + length
        if marker == 0xDA:
            return Probe(dims and data.rstrip(b"\x00").endswith(b"\xff\xd9"), None if dims else "corrupt", 1)
    return _bad("too_large")


_OBJECT = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+obj\b")
_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")


def _pdf_objects(data: bytes, limits: ParseLimits) -> dict[int, bytes] | None:
    marks = []
    for match in _OBJECT.finditer(data):
        marks.append(match)
        if len(marks) > limits.max_objects:
            return None
    objects: dict[int, bytes] = {}
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(data)
        objects[int(match.group(1))] = data[match.end() : end]
    return objects


def _probe_pdf(data: bytes, limits: ParseLimits) -> Probe:
    objects = _pdf_objects(data, limits)
    if not data.startswith(b"%PDF-") or objects is None:
        return _bad("too_large" if objects is None else "corrupt")
    if not objects or b"%%EOF" not in data[-1024:]:
        return _bad()
    pages = sum(len(_PAGE.findall(body)) for body in objects.values())
    if pages > limits.max_pages:
        return _bad("too_large")
    for body in objects.values():
        if _stream(body, limits) is None and b"stream" in body and b"endstream" in body:
            return _bad()
    return Probe(True, None, pages)


def probe(data: bytes, kind: str, limits: ParseLimits) -> Probe:
    if kind == "png":
        return _probe_png(data, limits)
    if kind == "jpeg":
        return _probe_jpeg(data, limits)
    if kind == "pdf":
        return _probe_pdf(data, limits)
    return _bad()


# --- text layer -------------------------------------------------------------------------------------------

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}


def _stream(body: bytes, limits: ParseLimits) -> bytes | None:
    match = _STREAM.search(body)
    if match is None:
        return b""
    raw = match.group(1)
    if b"/FlateDecode" not in body[: match.start()]:
        return raw[: limits.max_decoded_bytes + 1] if len(raw) <= limits.max_decoded_bytes else None
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(raw, limits.max_decoded_bytes + 1)
    except zlib.error:
        return None
    if len(out) > limits.max_decoded_bytes or inflater.unconsumed_tail:
        return None
    return out


def _literal(content: str, pos: int) -> tuple[str, int]:
    """The literal string starting at `pos` (the opening parenthesis) and the position after it."""
    depth, out, i = 0, [], pos
    while i < len(content):
        c = content[i]
        if c == "\\" and i + 1 < len(content):
            nxt = content[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(content) and j < i + 4 and content[j] in "01234567":
                    j += 1
                out.append(chr(int(content[i + 1 : j], 8) & 0xFF))
                i = j
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == "(":
            depth += 1
            if depth > 1:
                out.append(c)
        elif c == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out), len(content)


_TOKEN = re.compile(r"\(|\bT\*|\bTd\b|\bTD\b|\bET\b|\bTj\b|\bTJ\b|\bBT\b")


def _page_text(content: bytes, limit: int) -> str | None:
    """The page text, or None when it exceeds the bound: a cut page is never passed on as the whole page."""
    text = content.decode("latin-1")
    lines: list[str] = []
    current: list[str] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.search(text, pos)
        if match is None:
            break
        token = match.group(0)
        if token == "(":
            value, pos = _literal(text, match.start())
            current.append(value)
        else:
            pos = match.end()
            if token in ("T*", "Td", "TD", "ET") and current:
                lines.append("".join(current))
                current = []
        if sum(map(len, lines)) > limit:
            return None
    if current:
        lines.append("".join(current))
    joined = "\n".join(lines)
    return joined if len(joined) <= limit else None


_CONTENTS = re.compile(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)")
_REF = re.compile(rb"(\d{1,10})\s+\d{1,5}\s+R")


def pdf_pages(data: bytes, limits: ParseLimits) -> tuple[str, ...] | None:
    """The text of each page in file order, or None when the file is not well formed within the bounds."""
    objects = _pdf_objects(data, limits)
    if objects is None:
        return None
    pages: list[str] = []
    total = 0
    for number in sorted(objects):
        body = objects[number]
        if not _PAGE.search(body):
            continue
        parts = []
        found = _CONTENTS.search(body)
        for ref in _REF.finditer(found.group(1)) if found else ():
            stream = _stream(objects.get(int(ref.group(1)), b""), limits)
            if stream is None:
                return None
            text = _page_text(stream, limits.max_text_chars)
            if text is None:
                return None
            parts.append(text)
        page = "\n".join(p for p in parts if p)
        total += len(page)
        if len(pages) >= limits.max_pages or total > limits.max_text_chars:
            return None
        pages.append(page)
    return tuple(pages)


# --- proposals --------------------------------------------------------------------------------------------


def propose(
    pages: tuple[str, ...], vocabulary: dict[str, dict[str, object]], max_proposals: int
) -> tuple[Proposal, ...]:
    """Lines of the form `<label> <number> [unit]` whose label is a configured indicator label. The vocabulary
    (label to code and accepted units) comes from the pinned configuration release; nothing clinical is here.
    A value is kept as the source wrote it; a unit is kept only when the configuration accepts it for that code
    and is otherwise left empty for the person to supply (no default is ever filled)."""
    labels = sorted(vocabulary, key=lambda name: (-len(name), name))
    found: list[Proposal] = []
    for number, page in enumerate(pages, start=1):
        offset = 0
        for line in page.split("\n"):
            hit = _match_line(line, labels, vocabulary)
            if hit is not None:
                code, value, unit, start, end = hit
                found.append(Proposal(number, offset + start, offset + end, code, value, unit))
                if len(found) >= max_proposals:
                    return tuple(found)
            offset += len(line) + 1
    return tuple(found)


def _match_line(
    line: str, labels: list[str], vocabulary: dict[str, dict[str, object]]
) -> tuple[str, str, str | None, int, int] | None:
    lowered = line.lower()
    for label in labels:
        start = lowered.find(label)
        if start < 0 or (start > 0 and lowered[start - 1].isalnum()):
            continue
        rest = line[start + len(label) :]
        stripped = rest.lstrip(" \t:=")
        if len(stripped) == len(rest) and rest[:1].isalnum():
            continue
        match = _NUMBER.match(stripped)
        if match is None:
            continue
        after = stripped[match.end() :]
        if after[:1].isalnum():
            continue
        entry = vocabulary[label]
        token = after.strip().split(" ")[0] if after.strip() else ""
        unit = _unit(after, entry["units"])
        consumed = len(line) - len(after)
        if unit is not None:
            consumed += len(after) - len(after.lstrip()) + len(token)
        return str(entry["code"]), match.group(0), unit, start, min(len(line), max(consumed, start + 1))
    return None


def _unit(after: str, units: object) -> str | None:
    token = after.strip().split(" ")[0] if after.strip() else ""
    if not token or not isinstance(units, dict):
        return None
    mapped = units.get(token)
    return mapped if isinstance(mapped, str) else None
