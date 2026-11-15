"""Logical identity, intent keys, payload hashes and completion ids (E22 DE22-3, 3.1).

Identity is a JSON array in canonical form, so separator characters inside ids cannot collide. The completion id is a
version 5 UUID of the intent key (computed here with SHA-1 as RFC 4122 defines it), so a rebuild on any hub gives the
same id for the same basis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

from crew_health.participation.domain.errors import InvalidBasis
from crew_health.participation.domain.types import CompletionBasis

# RFC 4122 namespace for URLs, an ordinary implementation constant that carries no domain meaning.
_NAMESPACE: Final[bytes] = bytes.fromhex("6ba7b8119dad11d180b400c04fd430c8")
_NAME_PREFIX: Final[str] = "crew-health:participation:completion:"

type Json = None | bool | int | str | Sequence[Json] | Mapping[str, Json]


def canonical_json(value: Json) -> str:
    """RFC 8785 form for the values used here: no floats, keys sorted, no whitespace."""
    return json.dumps(_checked(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checked(value: Json) -> Json:
    if isinstance(value, float):
        raise InvalidBasis("floats are not part of an identity")
    if isinstance(value, Mapping):
        return {str(k): _checked(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_checked(v) for v in value]
    return value


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def logical_identity(basis: CompletionBasis) -> list[str]:
    if basis.basis == "episode_updated":
        return [
            basis.basis,
            _text(basis.basis_ref),
            basis.patient_id,
            _text(basis.cause_kind),
            _text(basis.cause_ref),
        ]
    return [basis.basis, basis.basis_ref, basis.patient_id]


def _text(value: str | None) -> str:
    if value is None:
        raise InvalidBasis("episode_updated needs its cause")
    return value


def replacement_identity(lineage_id: str, generation: int) -> list[Json]:
    return ["replacement", lineage_id, generation]


def correction_identity(lineage_id: str, correction_ref: str) -> list[Json]:
    return ["correction", lineage_id, correction_ref]


def reconcile_identity(cause: str, cause_ref: str) -> list[Json]:
    return ["reconcile", cause, cause_ref]


def intent_key(identity: Sequence[Json]) -> str:
    return sha256_hex(canonical_json(list(identity)))


def _instant(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def payload_hash(basis: CompletionBasis) -> str:
    """SHA-256 over the canonical form of every basis field."""
    fields: dict[str, Json] = {
        "basis": basis.basis,
        "basis_ref": basis.basis_ref,
        "patient_id": basis.patient_id,
        "mission_id": basis.mission_id,
        "origin_mode": basis.origin_mode,
        "occurred_at": _instant(basis.occurred_at),
        "original_offset": basis.original_offset,
        "actor_person_id": basis.actor_person_id,
        "cause_kind": basis.cause_kind,
        "cause_ref": basis.cause_ref,
        "required_answers_complete": basis.required_answers_complete,
    }
    return sha256_hex(canonical_json(fields))


def completion_id(key: str) -> str:
    """UUIDv5 of the intent key under the fixed namespace (RFC 4122 section 4.3)."""
    digest = bytearray(hashlib.sha1(_NAMESPACE + (_NAME_PREFIX + key).encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    hexed = digest.hex()
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"


def award_id(patient_id: str, mission_id: str, origin_mode: str, threshold: int) -> str:
    """Deterministic award id: pat_ and 24 hex of SHA-256 over the identity array (E22 3.2)."""
    return "pat_" + sha256_hex(canonical_json([patient_id, mission_id, origin_mode, threshold]))[:24]
