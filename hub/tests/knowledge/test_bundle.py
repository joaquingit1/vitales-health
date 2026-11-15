import gzip
import io
import tarfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.testing import MemorySettings
from crew_health.knowledge.adapters.bundle import BundleMember, verify_archive
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.module import setting_specs


def archive(rows: tuple[tuple[str, bytes, bytes], ...]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as output:
        for name, body, kind in rows:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(body)
            output.addfile(info, io.BytesIO(body))
    return gzip.compress(stream.getvalue(), mtime=0)


def expected(name: str, body: bytes) -> BundleMember:
    return BundleMember(name, sha256(body).hexdigest(), len(body))


@pytest.mark.trace("T-E10-13", "T-E10-47", "T-E10-55", "I6", "I8")
def test_archive_verifies_offline_without_publishing(settings: MemorySettings) -> None:
    body = Path("knowledge/inputs/package/NASA-SOURCES.md").read_bytes()
    member = expected("sources/NASA-SOURCES.md", body)
    bundle = archive(((member.name, body, tarfile.REGTYPE),))
    assert verify_archive(bundle, (member,), settings) == (member,)
    with pytest.raises(EvidenceInvalid):
        verify_archive(bundle, (replace(member, sha256="0" * 64),), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(bundle, (replace(member, byte_count=member.byte_count + 1),), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(bundle, (member, member), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(bundle, (), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(archive(()), (member,), settings)


@pytest.mark.trace("T-E10-19", "T-E10-55", "I7", "I8")
@pytest.mark.parametrize("name", ("/private", "../private", "a/../private", "a//b", "./a", "a\\b"))
def test_archive_rejects_noncanonical_paths(name: str, settings: MemorySettings) -> None:
    with pytest.raises(EvidenceInvalid):
        verify_archive(archive(((name, b"", tarfile.REGTYPE),)), (expected(name, b""),), settings)


@pytest.mark.trace("T-E10-19", "T-E10-55", "I7", "I8")
@pytest.mark.parametrize(
    "kind",
    (
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.DIRTYPE,
        tarfile.CHRTYPE,
        tarfile.FIFOTYPE,
        tarfile.XHDTYPE,
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_SPARSE,
    ),
)
def test_archive_rejects_nonregular_members(kind: bytes, settings: MemorySettings) -> None:
    bundle = archive((("source", b"", kind),))
    with pytest.raises(EvidenceInvalid):
        verify_archive(bundle, (expected("source", b""),), settings)


@pytest.mark.trace("T-E10-13", "T-E10-55", "I6", "I8")
def test_archive_rejects_duplicate_unknown_and_missing_members(settings: MemorySettings) -> None:
    first = ("source", b"", tarfile.REGTYPE)
    with pytest.raises(EvidenceInvalid):
        verify_archive(archive((first, first)), (expected("source", b""),), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(archive((first,)), (expected("other", b""),), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(archive((first,)), (expected("source", b""), expected("other", b"")), settings)


@pytest.mark.trace("T-E10-13", "T-E10-55", "I6", "I8")
def test_archive_truncation_rejected(settings: MemorySettings) -> None:
    @given(st.binary(min_size=1, max_size=128))
    def check(body: bytes) -> None:
        bundle = archive((("source", body, tarfile.REGTYPE),))
        member = expected("source", body)
        assert verify_archive(bundle, (member,), settings) == (member,)
        with pytest.raises(EvidenceInvalid):
            verify_archive(bundle[:-1], (member,), settings)

    check()


@pytest.mark.trace("T-E10-13", "T-E10-55", "T-E10-56", "I6", "I8")
def test_archive_resource_limits_precede_body_read(settings: MemorySettings) -> None:
    from decimal import Decimal

    from crew_health.kernel.units import Quantity, Unit

    body = b"x" * 4096
    bundle = archive((("source", body, tarfile.REGTYPE),))
    member = expected("source", body)
    for key, limit in (
        ("knowledge.bundle.max_compressed_bytes", len(bundle) - 1),
        ("knowledge.bundle.max_member_bytes", len(body) - 1),
        ("knowledge.bundle.max_total_expanded_bytes", len(body) - 1),
    ):
        values = dict(settings.values)
        values[key] = Quantity(Decimal(limit), Unit("By"))
        with pytest.raises(EvidenceInvalid):
            verify_archive(bundle, (member,), MemorySettings(setting_specs(), values))
    raw = gzip.decompress(bundle)
    with pytest.raises(EvidenceInvalid):
        verify_archive(gzip.compress(raw[: 512 + len(body)], mtime=0), (member,), settings)
    damaged = bytearray(raw)
    damaged[0] ^= 1
    with pytest.raises(EvidenceInvalid):
        verify_archive(gzip.compress(damaged, mtime=0), (member,), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(gzip.compress(raw + b"unexpected", mtime=0), (member,), settings)
    with pytest.raises(EvidenceInvalid):
        verify_archive(b"invalid gzip", (member,), settings)
