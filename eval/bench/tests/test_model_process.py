from __future__ import annotations

import json
import os
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator

import pytest
from crew_health.kernel.ids import KernelIdFactory
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.documents import BenchSettings, read_json
from crew_health_bench.adapters.model_generator import IsolatedModelGenerator
from crew_health_bench.adapters.model_release import load_release
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.domain.healthbench import HealthBenchVisible

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parents[1] / "model/schemas/model-release.schema.json"


@contextmanager
def model_twin() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return None

        def do_GET(self) -> None:
            assert self.path == "/props"
            self._reply({"model_path": "b" * 64 + ".gguf", "default_generation_settings": {"n_ctx": 1}})

        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            length = int(self.headers["Content-Length"])
            assert length < 1048576
            requests.append(json.loads(self.rfile.read(length)))
            self._reply({"choices": [{"message": {"content": "protocol completion"}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        def _reply(self, value: object) -> None:
            raw = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 2
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_port), requests
        finally:
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.mark.trace("V33", "R22", "R23")
def test_model_runs_in_separate_keyless_process_with_prompt_only(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    source = ROOT / "fixtures/model-release.json"
    release = load_release(source, SCHEMA, max_file_bytes=1048576)
    hidden = tmp_path / "rubric.json"
    hidden.write_text('{"rubric":"NEVER_SENT_RUBRIC"}', encoding="utf-8")
    settings = BenchSettings(read_json(ROOT / "config/bench-settings.json", max_file_bytes=1048576))
    clock = MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_generation")
    with model_twin() as (url, requests), FileArtifactStore(
            tmp_path / "generation", max_line_bytes=1048576, max_records=10) as store:
        generator = IsolatedModelGenerator(
            config_dir=ROOT / "config", release_path=source, release=release, schema=SCHEMA, base_url=url,
            settings=settings, environment={**os.environ, "CHC_EVAL_IDENTITY": "owner-secret",
                                           "OPENAI_API_KEY": "grader-secret"},
            grader_key_env="OPENAI_API_KEY", clock=clock,
            ids=KernelIdFactory(new_uuid_hex=lambda: "0" * 32), store=store, run_id="benchrun_protocol",
            cases={"protocol": HealthBenchVisible("protocol", (("user", "visible complaint"),))},
        )
        result = generator.generate("protocol", 0)
        assert result.status == "completed" and result.model_calls == 1
        assert result.wall == Quantity(Decimal(0), Unit("s"))
        assert requests[0]["messages"] == [{"role": "user", "content": "visible complaint"}]
        raw = (tmp_path / "generation" / result.output_ref).read_text(encoding="utf-8")
        assert "protocol completion" in raw
        assert all(secret not in raw + json.dumps(requests)
                   for secret in ("owner-secret", "grader-secret", "NEVER_SENT_RUBRIC"))
