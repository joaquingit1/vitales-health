from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from crew_health.knowledge.adapters.ed25519 import Ed25519Signer, Ed25519Verifier
from crew_health.knowledge.domain.errors import EvidenceInvalid


@pytest.mark.trace("T-E10-13", "T-E10-13b", "I6")
def test_signed_manifest_is_bound_to_trusted_key_and_exact_bytes() -> None:
    raw = Path("fixtures/knowledge/manifest.json").read_bytes()
    seed = sha256(raw).digest()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    signer = Ed25519Signer(key_id="key_fixture", private_key=seed)
    signature = signer.sign(raw)
    verifier = Ed25519Verifier({"key_fixture": key.public_key().public_bytes_raw()})
    assert verifier.verify(raw, signature, origin_mode="LIVE", allow_unsigned_fixture=False)
    for invalid in (
        replace(signature, key_id="key_unknown"),
        replace(signature, signature="00" * 64),
        replace(signature, manifest_sha256="0" * 64),
    ):
        with pytest.raises(EvidenceInvalid):
            verifier.verify(raw, invalid, origin_mode="LIVE", allow_unsigned_fixture=False)
    with pytest.raises(EvidenceInvalid):
        verifier.verify(raw + b" ", signature, origin_mode="LIVE", allow_unsigned_fixture=False)


@pytest.mark.trace("T-E10-13b", "I6")
@pytest.mark.parametrize("origin_mode", ("LIVE", "REPLAY", "SIM"))
def test_unsigned_fixture_never_promotes_to_operational_origin(origin_mode: str) -> None:
    raw = Path("fixtures/knowledge/manifest.json").read_bytes()
    verifier = Ed25519Verifier({})
    if origin_mode == "SIM":
        assert verifier.verify(raw, None, origin_mode=origin_mode, allow_unsigned_fixture=True) is False
    else:
        with pytest.raises(EvidenceInvalid):
            verifier.verify(raw, None, origin_mode=origin_mode, allow_unsigned_fixture=True)
    with pytest.raises(EvidenceInvalid):
        verifier.verify(raw, None, origin_mode=origin_mode, allow_unsigned_fixture=False)
