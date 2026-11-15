from __future__ import annotations

from hashlib import sha256
from unicodedata import normalize


def normalized(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().split())


def shingles(text: str, size: int) -> frozenset[str]:
    if size < 1:
        raise ValueError("shingle size must be positive")
    words = normalized(text).split()
    if len(words) < size:
        return frozenset((" ".join(words),)) if words else frozenset()
    return frozenset(" ".join(words[i : i + size]) for i in range(len(words) - size + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def fingerprints(text: str, *, fp_salt: str) -> frozenset[str]:
    return frozenset(sha256((fp_salt + shingle).encode()).hexdigest()[:16] for shingle in shingles(text, 8))
