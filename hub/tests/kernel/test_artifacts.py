import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.artifacts import ArtifactReference, PublicationReceipt
from crew_health.kernel.errors import ProblemError


@pytest.mark.trace("I6", "T-E10-13", "T-E10-54")
@given(st.binary(min_size=32, max_size=32), st.integers(min_value=0, max_value=2**30))
def test_artifact_identity_preserves_exact_hash_and_byte_count(digest: bytes, size: int) -> None:
    reference = ArtifactReference(digest.hex(), size)
    assert reference.sha256 == digest.hex()
    assert reference.byte_count == size
    assert PublicationReceipt("receipt_artifact", reference).reference == reference


@pytest.mark.trace("I6", "T-E10-55")
@pytest.mark.parametrize("digest,size", (("a" * 63, 1), ("A" * 64, 1), ("a" * 64, -1), ("a" * 64, True)))
def test_invalid_artifact_reference_fails_closed(digest: str, size: int) -> None:
    with pytest.raises(ProblemError):
        ArtifactReference(digest, size)
