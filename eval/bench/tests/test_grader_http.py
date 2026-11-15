import json
from pathlib import Path

import httpx
import pytest
from crew_health.kernel.units import Quantity

from crew_health_bench.adapters.documents import BenchSettings, load_document
from crew_health_bench.adapters.grader_http import GraderHTTP
from crew_health_bench.domain.documents import object_map
from crew_health_bench.domain.errors import BenchError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.trace("R22", "R23", "T-E18-14", "T-E18-15")
def test_grader_429_and_503_have_bounded_retries_and_no_secret_in_failure() -> None:
    settings = BenchSettings(load_document(ROOT / "config/bench-settings.json", max_file_bytes=65536))
    grader = object_map(load_document(ROOT / "config/healthbench-plan.json", max_file_bytes=65536)["grader"])
    requests: list[httpx.Request] = []
    sleeps: list[Quantity] = []

    def service(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429 if len(requests) == 1 else 503, json={"private": "never logged"})

    with httpx.Client(transport=httpx.MockTransport(service), trust_env=False) as client:
        adapter = GraderHTTP(client=client, base_url="http://127.0.0.1", grader=grader,
                             environment={"OPENAI_API_KEY": "secret-grader-key"}, settings=settings,
                             sleep=sleeps.append)
        with pytest.raises(BenchError, match="grader_unavailable") as failure:
            adapter.complete((("user", "hidden rubric"),), settings.quantity("bench.grader.call_timeout"))
        assert len(requests) == 1 + settings.integer("bench.grader.max_retries")
        assert len(sleeps) == settings.integer("bench.grader.max_retries")
        assert adapter.calls == adapter.errors == len(requests)
        assert "secret-grader-key" not in str(failure.value)
        payload = json.loads(requests[0].content)
        assert payload["max_tokens"] == grader["max_tokens"]
        assert payload["temperature"] == grader["temperature"]
        assert payload["messages"][0]["content"] == grader["system_message"]
        assert all(delay.value <= settings.quantity("bench.grader.backoff_cap").value for delay in sleeps)


@pytest.mark.trace("R22", "T-E18-14")
def test_success_and_missing_credential_are_distinct() -> None:
    settings = BenchSettings(load_document(ROOT / "config/bench-settings.json", max_file_bytes=65536))
    grader = object_map(load_document(ROOT / "config/healthbench-plan.json", max_file_bytes=65536)["grader"])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"criteria_met":true}'}}]})), trust_env=False) as client:
        adapter = GraderHTTP(client=client, base_url="http://127.0.0.1", grader=grader,
                             environment={"OPENAI_API_KEY": "test"}, settings=settings, sleep=lambda _: None)
        assert adapter.complete((("user", "protocol"),), settings.quantity("bench.grader.call_timeout"))
        adapter = GraderHTTP(client=client, base_url="http://127.0.0.1", grader=grader,
                             environment={}, settings=settings, sleep=lambda _: None)
        with pytest.raises(BenchError, match="grader_credential_missing") as refused:
            adapter.complete((("user", "protocol"),), settings.quantity("bench.grader.call_timeout"))
        assert adapter.calls == 0
        assert refused.value.exit_code == 6
        assert "OPENAI_API_KEY" in refused.value.detail and "Q-E18-1" in refused.value.detail
