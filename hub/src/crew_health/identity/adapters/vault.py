"""The E02 `PrivateKeyVault` location, unavailable until a live-vault backend is named (E02 5.2, R-E02-20).

No supported backend exists in this repository, so this adapter allocates, wraps and unwraps nothing.
Every call refuses with CAPABILITY_UNAVAILABLE and every revocation check answers "revoked", so private
writes stay off and nothing already sealed can be opened through it. It is not a test vault and keeps no
key material of any kind.
"""

from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.private import RevocationReceipt, SealedBlob, SealedPrivateRef
from crew_health.kernel.uow import SessionContext


def _unavailable() -> ProblemError:
    return ProblemError(
        ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value, retryable=False
    )


class UnavailableVault:
    def create_entry_key(self, *, owner_person_id: str, entry_id: str) -> str:
        raise _unavailable()

    def revoke(
        self, *, owner_person_id: str, entry_ids: tuple[str, ...], command_key: str
    ) -> RevocationReceipt:
        raise _unavailable()

    def revocation(self, revocation_id: str) -> RevocationReceipt:
        raise _unavailable()

    def is_revoked(self, *, entry_id: str) -> bool:
        return True

    def fingerprint(self, *, owner_person_id: str, canonical_identity_and_body: bytes) -> str:
        raise _unavailable()

    def encrypt(self, *, key_id: str, plaintext: bytes, associated_data: bytes) -> SealedBlob:
        raise _unavailable()

    def decrypt(
        self,
        session: SessionContext,
        *,
        reference: SealedPrivateRef,
        blob: SealedBlob,
        associated_data: bytes,
    ) -> bytes:
        raise _unavailable()
