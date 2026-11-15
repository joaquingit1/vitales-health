"""MedQA proxy command against a loopback stand-in: model validation of a general medical knowledge proxy,
never clinical validation. No real model, no network beyond 127.0.0.1."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.medqa import cli, run
from crew_health_bench.medqa.config import load_config
from crew_health_bench.medqa.domain import parse_answer, preregister, score
from crew_health_bench.medqa.model import ChatClient

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "INV-E18-05")]
CONFIG = Path(__file__).parents[1] / "config" / "medqa-proxy.json"
LETTERS = ("A", "B", "C", "D")
# Neutral stand-in items: the reply each question gets from the stand-in, and the key.
REPLIES = {"q1": "B", "q2": "Answer: C", "q3": "C", "q4": "I am not sure", "q5": "SLOW", "q6": "A"}
KEYS = {"q1": "B", "q2": "C", "q3": "D", "q4": "A", "q5": "A", "q6": "A"}


class _StandIn(BaseHTTPRequestHandler):
    payload = b""

    def log_message(self, *_: object) -> None:
        return

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(self.payload)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        question = body["messages"][1]["content"].split("\n", 1)[0]
        reply = REPLIES[question]
        if reply == "SLOW":
            time.sleep(1.0)
        self._send(json.dumps({"choices": [{"message": {"content": reply}}]}).encode())


@pytest.fixture
def stand_in() -> Iterator[str]:
    lines = [
        json.dumps({"question": q, "options": {k: f"option {k}" for k in LETTERS}, "answer_idx": KEYS[q]})
        for q in sorted(REPLIES)
    ]
    _StandIn.payload = ("\n".join(lines) + "\n").encode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _config(tmp_path: Path, url: str, **generation: object) -> Path:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    data = _StandIn.payload
    document["dataset"].update(
        url=url + "/data.jsonl", sha256=sha256(data).hexdigest(), size_bytes=len(data), examples=len(REPLIES)
    )
    document["generation"].update(item_timeout_s=0.3, **generation)
    document["sample"].update(max_n=len(REPLIES), warmup_n=0)
    path = tmp_path / "medqa-proxy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _cli(config: Path, cache: Path, *argv: str) -> int:
    return cli.main(["--config", str(config), "--cache", str(cache), *argv])


@pytest.mark.parametrize(
    ("reply", "letter"),
    [("B", "B"), (" Answer: C", "C"), ("(D)", "D"), ("A.", "A"), ("**B**", None), ("**B", "B"),
     ("b", None), ("E", None), ("", None), (None, None), ("The answer", None), ("BD", None)],
)  # fmt: skip
def test_parse_rule_is_strict(reply: str | None, letter: str | None) -> None:
    assert parse_answer(reply, LETTERS) == letter


@given(st.text(max_size=40))
def test_parse_never_invents_a_letter_outside_the_options(reply: str) -> None:
    assert parse_answer(reply, LETTERS) in (*LETTERS, None)


def test_sample_is_seeded_and_disjoint_from_warmup() -> None:
    ids = tuple(f"id{i:03d}" for i in range(50))
    first = preregister(ids, seed=7, n=20, warmup_n=5, max_n=40)
    assert first == preregister(tuple(reversed(ids)), seed=7, n=20, warmup_n=5, max_n=40)
    assert not set(first.item_ids) & set(first.warmup_ids)
    assert preregister(ids, seed=8, n=20, warmup_n=5, max_n=40).item_ids != first.item_ids
    for n in (0, 41, 46):
        with pytest.raises(BenchError):
            preregister(ids, seed=7, n=n, warmup_n=5, max_n=50 if n == 46 else 40)


def test_failures_and_missing_rows_stay_in_the_denominator() -> None:
    result = score(("correct", "timeout", "unparseable", "wrong"), n=10)
    assert (result.n, result.correct, result.timeout, result.accuracy) == (10, 1, 7, 0.1)
    zero = score(("timeout",), n=10)
    assert zero.accuracy == 0.0
    assert zero.interval[0] == 0.0
    assert round(zero.interval[1], 4) == 0.2775  # Wilson 95% upper bound for 0 of 10, by hand
    with pytest.raises(BenchError):
        score(("correct",) * 3, n=2)


def test_end_to_end_against_stand_in(tmp_path: Path, stand_in: str) -> None:
    config, cache = _config(tmp_path, stand_in), tmp_path / "cache"
    assert _cli(config, cache, "fetch") == 0
    assert _cli(config, cache, "run", "--stand-in", stand_in) == 5  # nothing runs before preregistration
    assert _cli(config, cache, "preregister", "--n", "6") == 0
    assert _cli(config, cache, "preregister", "--n", "6") == 5  # preregistration is never overwritten
    assert _cli(config, cache, "run", "--stand-in", stand_in) == 0
    report = json.loads((cache / "report.json").read_text(encoding="utf-8"))
    assert report["integrity"] == "stand_in_not_a_score"
    assert "not clinical validation" in report["label"]
    assert (report["n"], report["correct"], report["wrong"]) == (6, 3, 1)
    assert (report["unparseable"], report["timeout_or_missing"]) == (1, 1)
    assert report["accuracy"] == 0.5
    assert _cli(config, cache, "run", "--stand-in", stand_in) == 5  # no silent rerun over results


def test_hand_edited_preregistration_is_refused(tmp_path: Path, stand_in: str) -> None:
    config, cache = _config(tmp_path, stand_in), tmp_path / "cache"
    assert _cli(config, cache, "fetch") == 0
    assert _cli(config, cache, "preregister", "--n", "3") == 0
    path = cache / "preregistration.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["item_ids"] = sorted(document["item_ids"])[::-1]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert _cli(config, cache, "run", "--stand-in", stand_in) == 5
    assert not (cache / "results.jsonl").exists()


def test_dataset_hash_mismatch_is_refused(tmp_path: Path, stand_in: str) -> None:
    config = _config(tmp_path, stand_in)
    document = json.loads(config.read_text(encoding="utf-8"))
    document["dataset"]["sha256"] = "0" * 64
    config.write_text(json.dumps(document), encoding="utf-8")
    assert _cli(config, tmp_path / "cache", "fetch") == 4
    assert not [path for path in (tmp_path / "cache").rglob("*") if path.is_file()]


def test_model_outage_aborts_instead_of_scoring(tmp_path: Path, stand_in: str) -> None:
    config, cache = _config(tmp_path, stand_in), tmp_path / "cache"
    assert _cli(config, cache, "fetch") == 0
    assert _cli(config, cache, "preregister", "--n", "2") == 0
    assert _cli(config, cache, "run", "--stand-in", "http://127.0.0.1:9") == 6
    assert not (cache / "report.json").exists()


def test_only_loopback_stand_in_and_pinned_model(tmp_path: Path, stand_in: str) -> None:
    config, cache = _config(tmp_path, stand_in), tmp_path / "cache"
    assert _cli(config, cache, "fetch") == 0
    assert _cli(config, cache, "probe", "--stand-in", "http://example.org:80") == 2
    fake = tmp_path / "fake.gguf"
    fake.write_bytes(b"not the pinned weights")
    argv = ("--binary", str(fake), "--weights", str(fake), "--template", str(fake), "--port", "18999")
    assert _cli(config, cache, "probe", *argv) == 6


def test_config_edit_changes_the_request(tmp_path: Path, stand_in: str) -> None:
    edited = load_config(_config(tmp_path, stand_in, system="Edited instruction.", max_tokens=8))
    body = ChatClient(stand_in, edited.generation, connect_timeout_s=1, max_chars=10).body("q1")
    assert body["messages"] == [
        {"role": "system", "content": "Edited instruction."},
        {"role": "user", "content": "q1"},
    ]
    assert (body["max_tokens"], body["temperature"], body["chat_template_kwargs"]) == (
        8,
        0.0,
        {"enable_thinking": False},
    )


def test_config_refuses_sampling_and_missing_label(tmp_path: Path, stand_in: str) -> None:
    for change in ({"temperature": 0.7}, {"enable_thinking": True}):
        with pytest.raises(BenchError):
            load_config(_config(tmp_path, stand_in, **change))
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    document["label"] = "MedQA clinical validation"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BenchError):
        load_config(path)


def test_probe_uses_only_warmup_items(tmp_path: Path, stand_in: str) -> None:
    config_path = _config(tmp_path, stand_in)
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["sample"]["warmup_n"] = 2
    config_path.write_text(json.dumps(document), encoding="utf-8")
    config, cache = load_config(config_path), tmp_path / "cache"
    assert _cli(config_path, cache, "fetch") == 0
    items = cli._items(config, cache)
    sample = run.make_sample(config, items, 3)
    client = ChatClient(stand_in, config.generation, connect_timeout_s=1, max_chars=100)
    rows = run.probe(client, config, items, sample)
    assert [row["item_id"] for row in rows] == list(sample.warmup_ids)
    assert not set(sample.warmup_ids) & set(sample.item_ids)
