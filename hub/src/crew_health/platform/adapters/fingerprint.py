"""The command boundary's keyed body fingerprint (SYSTEM 5.8, AGENTS section 6).

A command's identity covers its operation, its target (the matched route's validated path IDs), its parameters and
its canonical body; `command_identity` composes those. This adapter supplies the body part for private command
bodies: HMAC-SHA256 under a per install secret, never a guessable plaintext hash. The secret is 32 random bytes in
a file beside the hub database, created once with owner only permissions and read on every start, so a retry after
a restart still replays. A missing directory, an unreadable file or a key of the wrong length fails loudly; the key is
never regenerated over an existing file, because a new key would turn every pending retry into a conflict."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

KEY_FILE = "command-fingerprint.key"
_KEY_BYTES = 32


class KeyedCommandFingerprint:
    """Structurally the `CommandFingerprint` port that measurement and episodes declare."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError("command fingerprint key has the wrong length")
        self._key = key

    @classmethod
    def beside(cls, database: Path) -> KeyedCommandFingerprint:
        return cls(_load_or_create(database.parent / KEY_FILE))

    def fingerprint(self, canonical_body: bytes) -> str:
        return hmac.new(self._key, canonical_body, hashlib.sha256).hexdigest()


def _load_or_create(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return path.read_bytes()
    try:
        key = secrets.token_bytes(_KEY_BYTES)
        os.write(descriptor, key)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path.read_bytes()
