"""Regression tests for the HealthBench scoring rules the cold review found broken (B1, B2, M7).

B1: a failed or timed-out generation is graded as the pinned harness grades an empty response
(INV-E18-05) and a grader-blocked example stays ungraded in the denominator, so the run can never read
full or complete. B2: the grader endpoint is pinned in `healthbench-plan.json`; any other URL, or a reply
naming another model, is refused. M7: 401 and 403 stop the command before anything is recorded,
and every 5xx is retried.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.documents import BenchSettings, load_document
from crew_health_bench.adapters.grader_http import GraderHTTP
from crew_health_bench.adapters.healthbench import OfficialGrader
from crew_health_bench.adapters.healthbench_grader import HealthBenchCaseGrader
from crew_health_bench.adapters.healthbench_reporter import HealthBenchReporter
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import OfficialGradeResult
from crew_health_bench.domain.runs import Generation

pytestmark = [pytest.mark.e18]

BENCH = Path(__file__).resolve().parents[1]
CONFIG = BENCH / "config"
RUN = "benchrun_scoring"
AT = datetime(2026, 11, 15, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, object]]] = {}

    def read_valid(self, run_id: str, stream: str) -> list[dict[str, object]]:
        return list(self.rows.get(stream, []))

    def append(self, run_id: str, stream: str, record: dict[str, object]) -> None:
        self.rows.setdefault(stream, []).append(dict(record))


class _Official:
    """Scores 0.9 for any non-empty completion and 0.0 for the empty response; can be told to block."""

    aggregate = OfficialGrader.aggregate

    def __init__(self, blocked: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str]] = []
        self.blocked = blocked

    def grade(self, prompt_id: str, response: str) -> OfficialGradeResult:
        self.calls.append((prompt_id, response))
        if prompt_id in self.blocked:
            raise BenchError("grader_unavailable")
        value = Quantity(Decimal("0.9") if response else Decimal(0), Unit("1"))
        return OfficialGradeResult(prompt_id, value, {"overall_score": value}, "", ())


class _Client:
    calls = 0
    errors = 0


def _generations(tmp_path: Path, statuses: tuple[str, ...]) -> tuple[Generation, ...]:
    result = []
    for index, status in enumerate(statuses):
        output = {"text": "completion " + str(index)}
        ref = "out-" + str(index) + ".json"
        (tmp_path / ref).write_bytes(canonical_json(output))
        result.append(
            Generation(
                "p" + str(index),
                0,
                status,
                AT,
                AT,
                "epoch",
                Quantity(Decimal(1), Unit("s")),
                sha256(canonical_json(output)).hexdigest(),
                ref,
                "trace",
                1,
            )
        )
    return tuple(result)


def _manifest(cases: tuple[str, ...], plan: dict[str, object]) -> dict[str, object]:
    return {
        "suite": "healthbench",
        "track": "model_only",
        "partition": "benchmark",
        "variant": "main",
        "case_set_sha256": sha256(canonical_json(cases)).hexdigest(),
        "dataset_sha256": {},
        "harness": {"commit": "652c89d0"},
        "model_release": {"release_id": "release"},
        "prompt_sha256": "none",
        "tool_catalog_sha256": "none",
        "evidence_release_id": "none",
        "evidence_sha256": "none",
        "registry_version": "none",
        "config_release_id": "none",
        "bench_documents_sha256": {},
        "grader": plan["grader"],
        "decoding": {},
        "seed": "18",
        "preregistration_sha256": "a" * 64,
        "hardware": {},
        "software": {},
        "integrity": "official",
        "repeats": 1,
    }


def _publish(
    tmp_path: Path, statuses: tuple[str, ...], official: _Official
) -> tuple[dict[str, object], dict[str, object]]:
    generations = _generations(tmp_path, statuses)
    store = _Store()
    grader = HealthBenchCaseGrader(
        official=official,  # type: ignore[arg-type]
        client=_Client(),  # type: ignore[arg-type]
        store=store,
        root=tmp_path,
        run_id=RUN,
        max_file_bytes=1 << 20,
    )
    grades = tuple(grader.grade(item) for item in generations)
    plan = dict(load_document(CONFIG / "healthbench-plan.json", max_file_bytes=1 << 20))
    cases = tuple(item.case_id for item in generations)
    reporter = HealthBenchReporter(
        official=official,  # type: ignore[arg-type]
        store=store,
        cases=cases,
        N=len(cases),
        case_salt=b"s" * 32,
        canaries=("canary",),
        policy=load_document(CONFIG / "claims-policy.json", max_file_bytes=1 << 20),
        plan=plan,
        validator=ArtifactSchemas(BENCH / "schemas", 1 << 20),
    )
    artifacts = reporter.build(RUN, _manifest(cases, plan), generations, grades)
    return json.loads(artifacts["aggregate.json"]), json.loads(artifacts["run_manifest.json"])


@pytest.mark.trace("INV-E18-05", "T-E18-14")
def test_timeouts_are_graded_as_the_empty_response_and_stay_in_the_score(tmp_path: Path) -> None:
    official = _Official()
    statuses = ("completed", "completed", "timeout", "failed_generation")
    aggregate, manifest = _publish(tmp_path, statuses, official)
    assert ("p2", "") in official.calls and ("p3", "") in official.calls
    block = aggregate["official"]
    assert isinstance(block, dict)
    assert block["graded"] == 4 and block["not_graded"] == 0 and block["graded_as_empty"] == 2
    assert block["score"] == pytest.approx(0.45)
    assert manifest["completeness"] == "full" and manifest["status"] == "complete"


@pytest.mark.trace("INV-E18-05", "INV-E18-06", "T-E18-14")
def test_a_grader_blocked_example_makes_the_run_partial_never_complete(tmp_path: Path) -> None:
    official = _Official(blocked=frozenset({"p1"}))
    aggregate, manifest = _publish(tmp_path, ("completed", "completed", "completed"), official)
    block = aggregate["official"]
    assert isinstance(block, dict)
    assert block["graded"] == 2 and block["not_graded"] == 1
    assert aggregate["completeness"] == "partial" and manifest["completeness"] == "partial"
    assert manifest["status"] == "partial"


def _settings() -> BenchSettings:
    return BenchSettings(load_document(CONFIG / "bench-settings.json", max_file_bytes=1 << 20))


def _grader(handler: httpx.MockTransport, *, base_url: str | None = None) -> GraderHTTP:
    plan = load_document(CONFIG / "healthbench-plan.json", max_file_bytes=1 << 20)
    grader = dict(plan["grader"])  # type: ignore[call-overload]
    return GraderHTTP(
        client=httpx.Client(transport=handler),
        base_url=base_url or str(grader["base_url"]),
        grader=grader,
        environment={"OPENAI_API_KEY": "k"},
        settings=_settings(),
        sleep=lambda _: None,
    )


def _reply(model: str) -> httpx.Response:
    return httpx.Response(
        200, json={"model": model, "choices": [{"message": {"role": "assistant", "content": "{}"}}]}
    )


@pytest.mark.trace("INV-E18-07", "T-E18-22")
def test_the_grader_endpoint_is_pinned_and_the_reply_must_name_the_pinned_model() -> None:
    pinned = "gpt-4.1-2025-04-14"
    with pytest.raises(BenchError) as error:
        _grader(httpx.MockTransport(lambda _: _reply(pinned)), base_url="http://127.0.0.1:8080")
    assert error.value.reason == "grader_unpinned"
    grader = _grader(httpx.MockTransport(lambda _: _reply("local-llama")))
    with pytest.raises(BenchError) as error:
        grader.complete((("user", "x"),), Quantity(Decimal(1), Unit("s")))
    assert error.value.reason == "grader_identity_mismatch"
    assert (
        _grader(httpx.MockTransport(lambda _: _reply(pinned))).complete(
            (("user", "x"),), Quantity(Decimal(1), Unit("s"))
        )
        == "{}"
    )


@pytest.mark.trace("T-E18-14")
@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_credential_stops_the_command_before_any_record(tmp_path: Path, status: int) -> None:
    grader = _grader(httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(BenchError) as error:
        grader.complete((("user", "x"),), Quantity(Decimal(1), Unit("s")))
    assert error.value.reason == "grader_credential_rejected" and grader.calls == 1


@pytest.mark.trace("T-E18-14")
@pytest.mark.parametrize("status", [500, 502, 504])
def test_every_5xx_is_retried_before_the_example_is_blocked(status: int) -> None:
    replies = [httpx.Response(status), _reply("gpt-4.1-2025-04-14")]
    grader = _grader(httpx.MockTransport(lambda _: replies.pop(0)))
    assert grader.complete((("user", "x"),), Quantity(Decimal(1), Unit("s"))) == "{}"
    assert grader.calls == 2 and grader.errors == 1
