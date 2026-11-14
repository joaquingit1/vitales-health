"""Append-only result store with a hash chain (21 CFR 11.10(e) style audit trail).

Owner: B1. Contract: docs/ENGINE-SPEC.md section 12.3.

Records live in a JSON Lines file. Each line is a canonical JSON object
``{"sequence", "recorded_at_utc", "actor", "reason", "kind", "payload", "prev_hash",
"record_hash"}`` where ``record_hash`` = SHA-256 of the canonical JSON of the record without
``record_hash`` and ``prev_hash`` is the previous record's hash (64 zeros for the first).
``recorded_at_utc`` lives outside the deterministic payload, so replays compare payloads.
"""

import dataclasses
import os
from pathlib import Path
from typing import Final

from pharmacy.config import canonical_json_bytes, parse_json_text, sha256_hex
from pharmacy.errors import ConfigLoadError, InputError, StoreError
from pharmacy.types import JSONObject, JSONValue, iso_to_seconds

GENESIS_HASH = "0" * 64

FIRST_SEQUENCE: Final[int] = 0
"""Sequence number of the first record. The spec does not fix it; 0 matches the genesis
link (the first record points at the all-zero hash) and Python indexing."""

_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {"sequence", "recorded_at_utc", "actor", "reason", "kind", "payload", "prev_hash", "record_hash"}
)
_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")
_HASH_LENGTH: Final[int] = 64


@dataclasses.dataclass(frozen=True, slots=True)
class StoredRecord:
    sequence: int
    recorded_at_utc: str
    actor: str
    reason: str
    kind: str
    payload: JSONValue
    prev_hash: str
    record_hash: str


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == _HASH_LENGTH and set(value) <= _HEX_DIGITS


def _unhashed(record: StoredRecord) -> JSONObject:
    return {
        "sequence": record.sequence,
        "recorded_at_utc": record.recorded_at_utc,
        "actor": record.actor,
        "reason": record.reason,
        "kind": record.kind,
        "payload": record.payload,
        "prev_hash": record.prev_hash,
    }


def _record_hash(record: StoredRecord) -> str:
    return sha256_hex(canonical_json_bytes(_unhashed(record)))


def _as_json(record: StoredRecord) -> JSONObject:
    out = _unhashed(record)
    out["record_hash"] = record.record_hash
    return out


def _parse_line(line: bytes, line_number: int) -> StoredRecord:
    where = f"line {line_number}"
    try:
        text = line.decode("utf-8")
        value = parse_json_text(text, where)
    except (UnicodeDecodeError, ConfigLoadError) as exc:
        raise StoreError(f"malformed record at {where}: {exc}") from exc
    if not isinstance(value, dict):
        raise StoreError(f"malformed record at {where}: not a JSON object")
    if set(value) != _RECORD_KEYS:
        raise StoreError(f"malformed record at {where}: keys {sorted(value)} differ from the record layout")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise StoreError(f"malformed record at {where}: sequence is not an integer")
    strings: dict[str, str] = {}
    for key in ("recorded_at_utc", "actor", "reason", "kind", "prev_hash", "record_hash"):
        item = value[key]
        if not isinstance(item, str):
            raise StoreError(f"malformed record at {where}: {key} is not a string")
        strings[key] = item
    for key in ("prev_hash", "record_hash"):
        if not _is_hash(strings[key]):
            raise StoreError(f"malformed record at {where}: {key} is not 64 lower-case hex digits")
    if canonical_json_bytes(value) != line:
        raise StoreError(f"malformed record at {where}: the line is not canonical JSON")
    return StoredRecord(
        sequence=sequence,
        recorded_at_utc=strings["recorded_at_utc"],
        actor=strings["actor"],
        reason=strings["reason"],
        kind=strings["kind"],
        payload=value["payload"],
        prev_hash=strings["prev_hash"],
        record_hash=strings["record_hash"],
    )


class ResultStore:
    """Append-only store. There is no update or delete; ``append`` is the only writer."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(
        self, kind: str, payload: JSONObject, actor: str, reason: str, recorded_at_utc: str
    ) -> StoredRecord:
        """Append one record and return it. The file is opened in append mode, the line is
        written with a single write call and flushed with fsync. Raises ``StoreError`` if the
        existing chain does not verify."""
        for name, text in (("kind", kind), ("actor", actor), ("reason", reason)):
            if not text:
                raise InputError(f"{name} must be a non-empty string")
        received: object = payload
        if not isinstance(received, dict):
            raise InputError("payload must be a JSON object")
        try:
            iso_to_seconds(recorded_at_utc)
        except ValueError as exc:
            raise InputError(f"recorded_at_utc is not an ISO 8601 UTC instant: {recorded_at_utc!r}") from exc
        try:
            payload_bytes = canonical_json_bytes(payload)
        except (ConfigLoadError, TypeError) as exc:
            raise InputError(f"payload cannot be stored as canonical JSON: {exc}") from exc
        existing = self.records()
        _verify_chain(existing)
        previous = existing[-1].record_hash if existing else GENESIS_HASH
        stored_payload = parse_json_text(payload_bytes.decode("utf-8"), "payload")
        draft = StoredRecord(
            sequence=FIRST_SEQUENCE + len(existing),
            recorded_at_utc=recorded_at_utc,
            actor=actor,
            reason=reason,
            kind=kind,
            payload=stored_payload,
            prev_hash=previous,
            record_hash="",
        )
        record = dataclasses.replace(draft, record_hash=_record_hash(draft))
        line = canonical_json_bytes(_as_json(record)) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def records(self) -> tuple[StoredRecord, ...]:
        """Every record in order. Raises ``StoreError`` on a malformed line."""
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise StoreError(f"cannot read the store {self.path}: {exc.strerror}") from exc
        if not data:
            return ()
        if not data.endswith(b"\n"):
            raise StoreError(f"malformed record at line {data.count(b'\n') + 1}: the last line is incomplete")
        lines = data[:-1].split(b"\n")
        return tuple(_parse_line(line, number) for number, line in enumerate(lines, start=1))

    def verify(self) -> None:
        """Check sequence numbers, prev_hash links and every record_hash. Raises
        ``StoreError`` naming the first bad record."""
        _verify_chain(self.records())


def _verify_chain(records: tuple[StoredRecord, ...]) -> None:
    previous = GENESIS_HASH
    for index, record in enumerate(records):
        expected_sequence = FIRST_SEQUENCE + index
        if record.sequence != expected_sequence:
            raise StoreError(
                f"record {index} (line {index + 1}): sequence {record.sequence} is not {expected_sequence}"
            )
        if record.prev_hash != previous:
            raise StoreError(f"record {record.sequence}: prev_hash does not link to the previous record")
        if record.record_hash != _record_hash(record):
            raise StoreError(f"record {record.sequence}: record_hash does not match its content")
        previous = record.record_hash
