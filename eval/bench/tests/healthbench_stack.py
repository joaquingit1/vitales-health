"""Loopback stand-ins for llama-server and the HealthBench grader, plus a disposable repository layout.

They exist only to drive the real `chc-bench healthbench` commands end to end without a model, a
grader credential or network access. Nothing they produce is a benchmark result: the grader stand-in
answers from a hash of the rubric line, so any number it yields measures the plumbing, never a model.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pyrage import x25519

BENCH = Path(__file__).resolve().parents[1]
REPOSITORY = BENCH.parents[1]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, status: int, body: dict[str, object]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path, {}))
        if self.path != "/props" or self.server.kind != "model":
            self._reply(404, {})
            return
        self._reply(
            200,
            {
                "model_path": "/models/" + self.server.weights_sha256 + ".gguf",
                "default_generation_settings": {"n_ctx": self.server.context_tokens},
            },
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.requests.append(("POST", self.path, {"authorization": self.headers.get("Authorization")}))
        if self.path != "/v1/chat/completions":
            self._reply(404, {})
            return
        if self.server.statuses:
            status = self.server.statuses.pop(0)
            if status != 200:
                self._reply(status, {"error": "scripted"})
                return
        if self.server.kind == "model":
            time.sleep(self.server.delay)
            last = body["messages"][-1]["content"]
            content = "Stand-in completion " + sha256(last.encode("utf-8")).hexdigest()[:12]
            self._reply(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
            return
        prompt = body["messages"][-1]["content"]
        met = sha256(prompt.encode("utf-8")).digest()[0] % 2 == 0
        verdict = json.dumps({"explanation": "stand-in", "criteria_met": met})
        self._reply(200, {"choices": [{"message": {"role": "assistant", "content": verdict}}]})


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, kind: str, *, weights_sha256: str, context_tokens: int, delay: float) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.kind, self.weights_sha256, self.context_tokens, self.delay = (
            kind,
            weights_sha256,
            context_tokens,
            delay,
        )
        self.requests: list[tuple[str, str, dict[str, str | None]]] = []
        self.statuses: list[int] = []

    @property
    def url(self) -> str:
        return "http://127.0.0.1:" + str(self.server_address[1])


@contextmanager
def serve(
    kind: str, *, weights_sha256: str = "", context_tokens: int = 0, delay: float = 0.0
) -> Iterator[_Server]:
    server = _Server(kind, weights_sha256=weights_sha256, context_tokens=context_tokens, delay=delay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def synthetic_row(index: int) -> dict[str, object]:
    return {
        "prompt_id": "synthetic-" + str(index),
        "prompt": [{"role": "user", "content": "Synthetic protocol prompt number " + str(index) + "."}],
        "rubrics": [
            {
                "criterion": "Synthetic positive criterion " + str(index),
                "points": 5,
                "tags": ["axis:protocol"],
            },
            {
                "criterion": "Synthetic second criterion "
                + str(index)
                + " tells the reader to rest, drink water and recheck",
                "points": 3,
                "tags": ["axis:protocol"],
            },
            {
                "criterion": "Synthetic negative criterion " + str(index),
                "points": -2,
                "tags": ["axis:protocol"],
            },
        ],
        "example_tags": ["theme:synthetic"],
        "ideal_completions_data": None,
        "canary": "synthetic-healthbench-canary",
    }


class Workspace:
    """A repository copy holding only what `chc-bench` reads.

    E18's config, schemas and vendor pin, plus E17's release schema.
    """

    def __init__(self, root: Path) -> None:
        self.repository = root / "repository"
        bench = self.repository / "eval/bench"
        for name in ("config", "schemas", "vendor"):
            shutil.copytree(BENCH / name, bench / name)
        (self.repository / "model/schemas").mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY / "model/schemas/model-release.schema.json",
            self.repository / "model/schemas/model-release.schema.json",
        )
        self.config = bench / "config"
        self.private = root / "private"
        self.runs = self.private / "runs"
        self.corpus = self.private / "corpus"
        self.published = root / "published"
        self.private.mkdir()
        self.release = self.private / "model-release.json"
        shutil.copyfile(BENCH / "fixtures/model-release.json", self.release)
        release = json.loads(self.release.read_bytes())
        self.weights_sha256 = str(release["weights"]["weights_sha256"])
        self.context_tokens = int(release["runtime"]["context_tokens"])
        self.identity = x25519.Identity.generate()
        self.identity_path = self.private / "identity.txt"
        self.identity_path.write_text(str(self.identity), encoding="utf-8")
        policy = json.loads((self.config / "partition-policy.json").read_bytes())
        policy["vault"]["recipients"] = [str(self.identity.to_public())]
        (self.config / "partition-policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
        self.salt = self.private / "case-salt.bin"
        self.salt.write_bytes(sha256(b"self-test salt").digest())

    def edit_plan(self, **changes: object) -> None:
        path = self.config / "healthbench-plan.json"
        plan = json.loads(path.read_bytes())
        plan.update(changes)
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    def synthetic_datasets(self, count: int) -> dict[str, Path]:
        sources: dict[str, Path] = {}
        rows = []
        plan = json.loads((self.config / "healthbench-plan.json").read_bytes())
        for dataset in plan["datasets"]:
            variant = str(dataset["variant"])
            raw = b"".join(
                (json.dumps(synthetic_row(index)) + "\n").encode("utf-8") for index in range(count)
            )
            source = self.private / ("source-" + variant + ".jsonl")
            source.write_bytes(raw)
            sources[variant] = source
            rows.append({**dataset, "sha256": sha256(raw).hexdigest(), "bytes": len(raw), "examples": count})
        self.edit_plan(datasets=rows)
        return sources

    def plan_input(self, *, variant: str, case_ids: list[str]) -> Path:
        from crew_health.kernel.commands import canonical_json

        manifest = {
            "suite": "healthbench",
            "track": "model_only",
            "partition": "benchmark",
            "variant": variant,
            "case_set_sha256": sha256(canonical_json(case_ids)).hexdigest(),
            "prompt_sha256": "none:model_only_track_adds_no_prompt",
            "tool_catalog_sha256": "none:model_only_track_has_no_tools",
            "evidence_release_id": "none:model_only_track_has_no_evidence",
            "evidence_sha256": "none:model_only_track_has_no_evidence",
            "registry_version": "none:model_only_track_has_no_registry",
            "config_release_id": "none:model_only_track_has_no_hub",
            "seed": "18",
            "repeats": 1,
            "hardware": {"profile": "harness self test"},
            "software": {"chc-bench": "0.1.0"},
        }
        path = self.private / ("plan-" + variant + ".json")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path
