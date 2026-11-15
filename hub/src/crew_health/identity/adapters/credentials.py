"""Argon2id verifiers and opaque bearer tokens (E02 3.2).

The verifier only checks a credential; it never derives a private-data key. Tokens are 256-bit random
secrets returned once; only their SHA-256 digest is stored.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from crew_health.identity.application.settings import Argon2Parameters
from crew_health.kernel.errors import ErrorCode, ProblemError

_TOKEN_BYTES = 32
_SALT_BYTES = 16
_HASH_BYTES = 32


class Argon2Verifier:
    """Fixed reviewed parameters (E02 11); unknown principals verify against a dummy at the same cost."""

    def __init__(self, parameters: Argon2Parameters) -> None:
        self._hasher = PasswordHasher(
            time_cost=parameters.iterations,
            memory_cost=parameters.memory_kib,
            parallelism=parameters.parallelism,
            hash_len=_HASH_BYTES,
            salt_len=_SALT_BYTES,
            type=Type.ID,
        )
        self._dummy = self._hasher.hash(secrets.token_urlsafe(_TOKEN_BYTES))

    def hash(self, secret: str) -> str:
        if not secret:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        return self._hasher.hash(secret)

    def verify(self, verifier: str | None, secret: str) -> bool:
        """True only for a real verifier that matches; a missing one still costs one full verification."""
        target = self._dummy if verifier is None else verifier
        try:
            matched = self._hasher.verify(target, secret)
        except VerifyMismatchError:
            return False
        except VerificationError, InvalidHashError:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value) from None
        return matched and verifier is not None


class OpaqueTokens:
    def new_token(self) -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES)).rstrip(b"=").decode("ascii")

    def digest(self, token: str) -> str:
        return hashlib.sha256(b"crew-health.session.v1\0" + token.encode("utf-8")).hexdigest()
