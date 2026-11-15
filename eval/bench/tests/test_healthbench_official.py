from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.documents import BenchSettings, read_json
from crew_health_bench.adapters.healthbench import HealthBenchCorpus, OfficialGrader, PreparedGrader
from crew_health_bench.domain.errors import BenchError

ROOT = Path(__file__).resolve().parents[1]


def row() -> dict[str, object]:
    return {
        "prompt_id": "protocol-arithmetic",
        "prompt": [{"role": "user", "content": "Return the protocol sentinel."}],
        "rubrics": [
            {"criterion": "positive sentinel", "points": 2, "tags": ["protocol"]},
            {"criterion": "absent sentinel", "points": 1, "tags": ["protocol"]},
            {"criterion": "negative sentinel", "points": -1, "tags": ["protocol"]},
        ],
        "example_tags": ["protocol-fixture"],
        "ideal_completions_data": None,
        "canary": "private-official-fixture",
    }


def settings() -> BenchSettings:
    return BenchSettings(read_json(ROOT / "config/bench-settings.json", max_file_bytes=1048576))


class Grader:
    def __init__(self, malformed: bool = False) -> None:
        self.malformed = malformed
        self.calls = 0

    def complete(self, messages: tuple[tuple[str, str], ...], timeout: Quantity) -> str:
        assert timeout.unit == Unit("s")
        self.calls += 1
        if self.malformed:
            return "{}"
        return json.dumps({"criteria_met": "absent sentinel" not in messages[0][1]})


def corpus(tmp_path: Path) -> HealthBenchCorpus:
    raw = (json.dumps(row()) + "\n").encode("utf-8")
    source = tmp_path / "official.jsonl"
    source.write_bytes(raw)
    return HealthBenchCorpus(source, sha256(raw).hexdigest(), len(raw), 1, settings())


@pytest.mark.trace("R22", "R23", "V33")
def test_original_official_scoring_and_private_prompt_projection(tmp_path: Path) -> None:
    source = corpus(tmp_path)
    visible = source.visible("protocol-arithmetic")
    assert visible.messages == (("user", "Return the protocol sentinel."),)
    assert "rubric" not in repr(visible)
    assert "private-official-fixture" not in repr(visible)
    client = Grader()
    prepared = PreparedGrader("candidate", "independent", client, settings())
    grader = OfficialGrader(source, prepared, ROOT / "config/healthbench-plan.json")
    result = grader.grade("protocol-arithmetic", "protocol output")
    assert result.score.value == Decimal(str(1 / 3))
    assert result.metrics["protocol-fixture"].value == result.score.value
    assert client.calls == 3
    aggregate = grader.aggregate((result,))
    assert aggregate["score"].value == result.score.value
    assert aggregate["score:n_samples"] == Quantity(Decimal(1), Unit("1"))


@pytest.mark.trace("R22", "R23", "invariant")
def test_grader_failure_is_bounded_and_never_zero(tmp_path: Path) -> None:
    source = corpus(tmp_path)
    client = Grader(malformed=True)
    prepared = PreparedGrader("candidate", "independent", client, settings())
    grader = OfficialGrader(source, prepared, ROOT / "config/healthbench-plan.json")
    with pytest.raises(BenchError, match="grader_call_limit"):
        grader.grade("protocol-arithmetic", "output")
    assert client.calls <= 3 * (1 + settings().integer("bench.grader.max_retries"))
    with pytest.raises(BenchError):
        PreparedGrader("same", "same", client, settings())


@pytest.mark.trace("R22", "R23", "invariant")
def test_dataset_pin_and_schema_fail_before_generation(tmp_path: Path) -> None:
    source = tmp_path / "official.jsonl"
    source.write_bytes(b"{}\n")
    with pytest.raises(BenchError, match="dataset_sha256"):
        HealthBenchCorpus(source, "0" * 64, 3, 1, settings())
    with pytest.raises(BenchError, match="healthbench_case_invalid"):
        HealthBenchCorpus(source, sha256(source.read_bytes()).hexdigest(), 3, 1, settings())
    raw = (json.dumps(row()) + "\n").encode("utf-8") * 2
    source.write_bytes(raw)
    with pytest.raises(BenchError, match="duplicate_prompt_id"):
        HealthBenchCorpus(source, sha256(raw).hexdigest(), len(raw), 2, settings())
