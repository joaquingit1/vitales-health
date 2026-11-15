"""Deterministic report inputs for the E09 tests (AGENTS 4: test data lives only in eval/).
Every file is built from bytes here, so a reviewer can read what a case contains. The clinical lines are
labels from the example indicator catalog and values typed by the test, never taken from product code."""

from __future__ import annotations

import struct
import zlib


def _escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def pdf(pages: list[list[str]], *, flate: bool = False, extra: bytes = b"") -> bytes:
    """A small PDF whose pages hold the given text lines (one `Tj` per line, `Td` between)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for index, lines in enumerate(pages):
        content = (
            b"BT /F1 12 Tf " + b" 0 -14 Td ".join(b"(" + _escape(line) + b") Tj" for line in lines) + b" ET"
        )
        header = b""
        if flate:
            content, header = zlib.compress(content), b" /Filter /FlateDecode"
        objects.append(f"<< /Type /Page /Parent 2 0 R /Contents {4 + 2 * index} 0 R >>".encode())
        objects.append(
            b"<< /Length " + str(len(content)).encode() + header + b" >>\nstream\n" + content + b"\nendstream"
        )
    body = b"%PDF-1.4\n"
    for number, obj in enumerate(objects, start=1):
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    return body + extra + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


def jpeg(width: int = 2, height: int = 2) -> bytes:
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    sos = b"\x01\x01\x00\x00\x3f\x00"
    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0"
        + struct.pack(">H", len(sof) + 2)
        + sof
        + b"\xff\xda"
        + struct.pack(">H", len(sos) + 2)
        + sos
        + b"\x12\x34"
        + b"\xff\xd9"
    )


def pdf_with_javascript() -> bytes:
    return pdf(
        [["Pulse at rest 72 bpm"]], extra=b"99 0 obj\n<< /S /JavaScript /JS (app.alert(1)) >>\nendobj\n"
    )


def truncated_pdf() -> bytes:
    return pdf([["Pulse at rest 72 bpm"]])[:-40]


def png_bad_crc() -> bytes:
    data = bytearray(png())
    data[-5] ^= 0xFF  # inside the IEND crc
    return bytes(data)


def png_huge_dimensions() -> bytes:
    """Valid structure, absurd declared size: refused by the pixel bound before any decode."""
    data = bytearray(png())
    data[16:24] = struct.pack(">II", 60000, 60000)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])))
    return bytes(data)


def pdf_bomb(size: int) -> bytes:
    """A flate stream that expands to `size` bytes of text: refused by the decode bound."""
    content = zlib.compress(b"(a) Tj " * (size // 7))
    obj = (
        b"<< /Length "
        + str(len(content)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + content
        + b"\nendstream"
    )
    base = pdf([["Pulse at rest 72 bpm"]]).replace(b"4 0 obj", b"4 0 obj", 1)
    return base.replace(b"trailer", b"77 0 obj\n" + obj + b"\nendobj\ntrailer", 1)
