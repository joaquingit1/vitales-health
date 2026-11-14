"""Unit tests of the append-only result store (spec 12.3)."""

import json
from pathlib import Path

import pytest

from pharmacy.config import canonical_json_bytes, sha256_hex
from pharmacy.errors import InputError, StoreError
from pharmacy.store import FIRST_SEQUENCE, GENESIS_HASH, ResultStore, StoredRecord
from pharmacy.types import JSONObject, JSONValue

T0 = "2028-04-19T00:00:00.000000Z"
T1 = "2028-04-20T00:00:00.000000Z"


def _store(tmp_path: Path) -> ResultStore:
    return ResultStore(tmp_path / "results.jsonl")


def _filled(tmp_path: Path, count: int = 3) -> ResultStore:
    store = _store(tmp_path)
    for i in range(count):
        store.append("inventory_assessment", {"index": i, "value": 0.1 * i}, "pharmacist", "scheduled", T0)
    return store


def _lines(store: ResultStore) -> list[dict[str, JSONValue]]:
    return [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]


def _write_lines(store: ResultStore, records: list[dict[str, JSONValue]]) -> None:
    store.path.write_bytes(b"".join(canonical_json_bytes(record) + b"\n" for record in records))


def _rehash(record: dict[str, JSONValue]) -> None:
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = sha256_hex(canonical_json_bytes(body))


def test_empty_store_has_no_records_and_verifies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.records() == ()
    store.verify()
    store.path.write_bytes(b"")
    assert store.records() == ()


def test_append_and_read_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload: JSONObject = {"b": [1, 2.5, None], "a": {"z": "x"}, "flag": True}
    first = store.append("estimate", payload, "engine", "assessment", T0)
    second = store.append("decision", {"state": "adjust"}, "engine", "assessment", T1)
    assert first.sequence == FIRST_SEQUENCE
    assert second.sequence == FIRST_SEQUENCE + 1
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash
    assert first.payload == payload
    assert store.records() == (first, second)
    store.verify()


def test_record_hash_is_sha256_of_the_record_without_it(tmp_path: Path) -> None:
    store = _filled(tmp_path, 1)
    (line,) = store.path.read_bytes().splitlines()
    record = json.loads(line)
    expected = record.pop("record_hash")
    assert sha256_hex(canonical_json_bytes(record)) == expected
    assert line == canonical_json_bytes({**record, "record_hash": expected})


def test_records_are_stored_one_canonical_line_each(tmp_path: Path) -> None:
    store = _filled(tmp_path, 3)
    data = store.path.read_bytes()
    assert data.endswith(b"\n")
    assert data.count(b"\n") == 3
    for line in data.splitlines():
        assert canonical_json_bytes(json.loads(line)) == line


def _tampered(tmp_path: Path, index: int, key: str, value: JSONValue, *, rehash: bool) -> ResultStore:
    store = _filled(tmp_path, 3)
    records = _lines(store)
    records[index][key] = value
    if rehash:
        _rehash(records[index])
    _write_lines(store, records)
    return store


def test_tampered_payload_is_detected(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 1, "payload", {"index": 99, "value": 0.1}, rehash=False)
    with pytest.raises(StoreError, match="record 1: record_hash"):
        store.verify()


def test_tampered_hash_breaks_the_next_link(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 0, "payload", {"index": 0, "value": 7.0}, rehash=True)
    with pytest.raises(StoreError, match="record 1: prev_hash"):
        store.verify()


def test_tampered_record_hash_is_detected(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 2, "record_hash", "f" * 64, rehash=False)
    with pytest.raises(StoreError, match="record 2: record_hash"):
        store.verify()


def test_broken_link_is_detected(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 2, "prev_hash", GENESIS_HASH, rehash=True)
    with pytest.raises(StoreError, match="record 2: prev_hash"):
        store.verify()


def test_first_record_must_link_to_genesis(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 0, "prev_hash", "1" * 64, rehash=True)
    with pytest.raises(StoreError, match="record 0: prev_hash"):
        store.verify()


def test_wrong_sequence_is_detected(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 1, "sequence", 5, rehash=True)
    with pytest.raises(StoreError, match="sequence 5 is not 1"):
        store.verify()


def test_deleted_record_is_detected(tmp_path: Path) -> None:
    store = _filled(tmp_path, 3)
    records = _lines(store)
    _write_lines(store, [records[0], records[2]])
    with pytest.raises(StoreError, match="sequence 2 is not 1"):
        store.verify()


def test_non_canonical_line_is_rejected(tmp_path: Path) -> None:
    store = _filled(tmp_path, 2)
    records = _lines(store)
    text = (
        canonical_json_bytes(records[0]).decode() + "\n" + json.dumps(records[1], indent=1).replace("\n", "")
    )
    store.path.write_text(text + "\n", encoding="utf-8")
    with pytest.raises(StoreError, match="line 2: the line is not canonical"):
        store.records()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{not json}\n", "line 1"),
        (b"[1,2]\n", "not a JSON object"),
        (b'{"a":1}\n', "differ from the record layout"),
        (b"\xff\xfe\n", "line 1"),
    ],
)
def test_malformed_lines_are_rejected(tmp_path: Path, content: bytes, message: str) -> None:
    store = _store(tmp_path)
    store.path.write_bytes(content)
    with pytest.raises(StoreError, match=message):
        store.records()


def _valid_record() -> dict[str, JSONValue]:
    record: dict[str, JSONValue] = {
        "sequence": 0,
        "recorded_at_utc": T0,
        "actor": "engine",
        "reason": "assessment",
        "kind": "estimate",
        "payload": {},
        "prev_hash": GENESIS_HASH,
        "record_hash": "",
    }
    _rehash(record)
    return record


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("sequence", "0", "sequence is not an integer"),
        ("sequence", True, "sequence is not an integer"),
        ("actor", 3, "actor is not a string"),
        ("prev_hash", "abc", "prev_hash is not 64"),
        ("record_hash", "A" * 64, "record_hash is not 64"),
    ],
)
def test_wrongly_typed_members_are_rejected(tmp_path: Path, key: str, value: JSONValue, message: str) -> None:
    store = _store(tmp_path)
    record = _valid_record()
    record[key] = value
    _write_lines(store, [record])
    with pytest.raises(StoreError, match=message):
        store.records()


def test_valid_hand_written_record_reads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_lines(store, [_valid_record()])
    (record,) = store.records()
    assert isinstance(record, StoredRecord)
    store.verify()


def test_incomplete_last_line_is_rejected(tmp_path: Path) -> None:
    store = _filled(tmp_path, 2)
    data = store.path.read_bytes()
    store.path.write_bytes(data[:-5])
    with pytest.raises(StoreError, match="line 2: the last line is incomplete"):
        store.records()


def test_append_refuses_to_extend_a_broken_chain(tmp_path: Path) -> None:
    store = _tampered(tmp_path, 1, "payload", {"index": 42}, rehash=False)
    before = store.path.read_bytes()
    with pytest.raises(StoreError):
        store.append("estimate", {"x": 1}, "engine", "assessment", T1)
    assert store.path.read_bytes() == before


def test_unreadable_store_raises_store_error(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)  # a directory cannot be read as a file
    with pytest.raises(StoreError, match="cannot read the store"):
        store.records()


@pytest.mark.parametrize(
    ("kind", "actor", "reason", "recorded_at"),
    [
        ("", "engine", "assessment", T0),
        ("estimate", "", "assessment", T0),
        ("estimate", "engine", "", T0),
        ("estimate", "engine", "assessment", "2028-04-19 00:00:00"),
    ],
)
def test_append_validates_its_metadata(
    tmp_path: Path, kind: str, actor: str, reason: str, recorded_at: str
) -> None:
    store = _store(tmp_path)
    with pytest.raises(InputError):
        store.append(kind, {"x": 1}, actor, reason, recorded_at)
    assert not store.path.exists()


def test_append_rejects_non_json_payloads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InputError, match="payload must be a JSON object"):
        store.append("estimate", [1, 2], "engine", "assessment", T0)  # type: ignore[arg-type]
    with pytest.raises(InputError, match="canonical JSON"):
        store.append("estimate", {"x": float("nan")}, "engine", "assessment", T0)
    with pytest.raises(InputError, match="canonical JSON"):
        store.append("estimate", {"x": {1, 2}}, "engine", "assessment", T0)  # type: ignore[dict-item]
    assert not store.path.exists()


def test_recorded_time_is_outside_the_payload(tmp_path: Path) -> None:
    first = ResultStore(tmp_path / "a.jsonl")
    second = ResultStore(tmp_path / "b.jsonl")
    one = first.append("estimate", {"x": 1}, "engine", "assessment", T0)
    two = second.append("estimate", {"x": 1}, "engine", "assessment", T1)
    assert one.payload == two.payload
    assert one.record_hash != two.record_hash
