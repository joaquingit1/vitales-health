from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.manifest import SignedManifest


class Ed25519Signer:
    def __init__(self, *, key_id: str, private_key: bytes) -> None:
        self._key_id = require_id(key_id)
        try:
            self._key = Ed25519PrivateKey.from_private_bytes(private_key)
        except ValueError as error:
            raise EvidenceInvalid("signing_key") from error

    def sign(self, manifest: bytes) -> SignedManifest:
        return SignedManifest(self._key_id, self._key.sign(manifest).hex(), sha256(manifest).hexdigest())


class Ed25519Verifier:
    def __init__(self, trusted_keys: Mapping[str, bytes]) -> None:
        self._keys: dict[str, Ed25519PublicKey] = {}
        try:
            for key_id, raw in sorted(trusted_keys.items()):
                self._keys[require_id(key_id)] = Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as error:
            raise EvidenceInvalid("verification_key") from error

    def verify(
        self,
        manifest: bytes,
        signature: SignedManifest | None,
        *,
        origin_mode: Literal["LIVE", "REPLAY", "SIM"],
        allow_unsigned_fixture: bool,
    ) -> bool:
        if origin_mode not in ("LIVE", "REPLAY", "SIM"):
            raise EvidenceInvalid("origin_mode")
        if signature is None:
            if origin_mode == "SIM" and allow_unsigned_fixture is True:
                return False
            raise EvidenceInvalid("signature_required")
        key = self._keys.get(signature.key_id)
        if key is None or sha256(manifest).hexdigest() != signature.manifest_sha256:
            raise EvidenceInvalid("signature_target")
        try:
            raw_signature = bytes.fromhex(signature.signature)
            key.verify(raw_signature, manifest)
        except (ValueError, InvalidSignature) as error:
            raise EvidenceInvalid("signature_invalid") from error
        return True
