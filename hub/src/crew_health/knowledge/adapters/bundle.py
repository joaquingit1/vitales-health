from __future__ import annotations

import gzip
import io
import tarfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.adapters.registry_file import byte_limit
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash


@dataclass(frozen=True, slots=True)
class BundleMember:
    name: str
    sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        _path(self.name)
        require_hash(self.sha256)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise EvidenceInvalid("bundle_member_bytes")


def _path(name: str) -> None:
    if not name or "\\" in name or ":" in name or any(p in ("", ".", "..") for p in name.split("/")):
        raise EvidenceInvalid("bundle_path")


def _read(stream: gzip.GzipFile, size: int) -> bytes:
    raw = stream.read(size)
    if len(raw) != size:
        raise EvidenceInvalid("bundle_truncated")
    return raw


def _header(raw: bytes) -> tarfile.TarInfo:
    if raw[257:265] != b"ustar\x0000":
        raise EvidenceInvalid("bundle_format")
    member = tarfile.TarInfo.frombuf(raw, encoding="utf-8", errors="strict")
    _path(member.name)
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.size < 0:
        raise EvidenceInvalid("bundle_member_type")
    return member


def _body_hash(stream: gzip.GzipFile, size: int, chunk_bytes: int) -> str:
    digest = sha256()
    remaining = size
    for _ in range((size + chunk_bytes - 1) // chunk_bytes):
        chunk = _read(stream, min(remaining, chunk_bytes))
        digest.update(chunk)
        remaining -= len(chunk)
    padding = (-size) % tarfile.BLOCKSIZE
    if _read(stream, padding) != bytes(padding):
        raise EvidenceInvalid("bundle_padding")
    return digest.hexdigest()


def _end(stream: gzip.GzipFile) -> None:
    if _read(stream, tarfile.BLOCKSIZE) != bytes(tarfile.BLOCKSIZE):
        raise EvidenceInvalid("bundle_end")
    padding = stream.read(tarfile.RECORDSIZE + 1)
    if len(padding) > tarfile.RECORDSIZE or any(padding):
        raise EvidenceInvalid("bundle_trailing_data")


def _members(
    stream: gzip.GzipFile, expected: dict[str, BundleMember], settings: HubSettings
) -> tuple[BundleMember, ...]:
    found: dict[str, BundleMember] = {}
    total = 0
    maximum = settings.integer("knowledge.bundle.max_members")
    for _ in range(maximum + 1):
        raw = _read(stream, tarfile.BLOCKSIZE)
        if raw == bytes(tarfile.BLOCKSIZE):
            _end(stream)
            if found.keys() != expected.keys():
                raise EvidenceInvalid("bundle_missing_member")
            return tuple(found[name] for name in sorted(found))
        require_bound(len(found) + 1, maximum)
        header = _header(raw)
        target = expected.get(header.name)
        if target is None or header.name in found:
            raise EvidenceInvalid("bundle_members")
        require_bound(header.size, byte_limit(settings, "knowledge.bundle.max_member_bytes"))
        total += header.size
        require_bound(total, byte_limit(settings, "knowledge.bundle.max_total_expanded_bytes"))
        if header.size != target.byte_count:
            raise EvidenceInvalid("bundle_member_bytes")
        digest = _body_hash(stream, header.size, byte_limit(settings, "knowledge.io.chunk_bytes"))
        if digest != target.sha256:
            raise EvidenceInvalid("bundle_member_sha256")
        found[header.name] = target
    raise EvidenceInvalid("bundle_members")


def verify_archive(
    raw: bytes, expected: tuple[BundleMember, ...], settings: HubSettings
) -> tuple[BundleMember, ...]:
    """Verify a gzip/ustar container against pinned members without publishing it.

    Container integrity alone does not establish a signed, source/purpose-eligible release.
    """
    require_bound(len(raw), byte_limit(settings, "knowledge.bundle.max_compressed_bytes"))
    require_bound(len(expected), settings.integer("knowledge.bundle.max_members"))
    inventory = {member.name: member for member in expected}
    if not inventory or len(inventory) != len(expected):
        raise EvidenceInvalid("bundle_members")
    for member in expected:
        require_bound(member.byte_count, byte_limit(settings, "knowledge.bundle.max_member_bytes"))
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            return _members(stream, inventory, settings)
    except (gzip.BadGzipFile, EOFError, zlib.error, tarfile.HeaderError, UnicodeError) as error:
        raise EvidenceInvalid("bundle_format") from error


class DeterministicBundleWriter:
    def __init__(self, *, settings: HubSettings) -> None:
        self._settings = settings

    def write(self, members: Sequence[tuple[str, bytes]]) -> bytes:
        require_bound(len(members), self._settings.integer("knowledge.bundle.max_members"))
        if len({name for name, _ in members}) != len(members):
            raise EvidenceInvalid("bundle_members")
        total = sum(len(raw) for _, raw in members)
        require_bound(total, byte_limit(self._settings, "knowledge.bundle.max_total_expanded_bytes"))
        require_bound(total * 3, byte_limit(self._settings, "knowledge.install.rss_bytes"))
        target = io.BytesIO()
        with gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(members):
                    member = BundleMember(name, sha256(raw).hexdigest(), len(raw))
                    require_bound(
                        member.byte_count, byte_limit(self._settings, "knowledge.bundle.max_member_bytes")
                    )
                    info = tarfile.TarInfo(member.name)
                    info.size, info.mode, info.mtime = member.byte_count, 0o600, 0
                    archive.addfile(info, io.BytesIO(raw))
        result = target.getvalue()
        require_bound(len(result), byte_limit(self._settings, "knowledge.bundle.max_compressed_bytes"))
        return result
