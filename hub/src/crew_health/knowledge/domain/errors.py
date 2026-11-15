from __future__ import annotations

import re


class EvidenceInvalid(ValueError):
    """Invalid public evidence; messages contain safe field names only."""


def require_hash(value: str) -> str:
    if re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise EvidenceInvalid("sha256")
    return value


def require_bound(size: int, maximum: int) -> None:
    if type(maximum) is not int or maximum < 1 or not 0 <= size <= maximum:
        raise EvidenceInvalid("bound")
