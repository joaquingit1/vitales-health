"""The pinned llama-server process and a bounded chat client for it."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import httpx

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.medqa.config import Generation, ModelPin, Runtime

_CHUNK = 1 << 20


def file_sha256(path: Path) -> str:
    checksum = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    file_name: str
    weights_sha256: str
    source: str
    chat_template_sha256: str
    binary_sha256: str
    runtime_version: str


def verify_pins(binary: Path, weights: Path, template: Path, pin: ModelPin) -> ModelIdentity:
    """Refuse anything that is not the E17 pinned binary, weights and chat template."""
    checks = (
        (binary.resolve(), pin.binary_sha256, "runtime_binary_mismatch"),
        (weights, pin.weights_sha256, "weights_hash_mismatch"),
        (template, pin.chat_template_sha256, "chat_template_mismatch"),
    )
    for path, expected, reason in checks:
        if not path.is_file() or file_sha256(path) != expected:
            raise BenchError(reason, exit_code=6)
    if weights.name != pin.file_name:
        raise BenchError("weights_name_mismatch", exit_code=6)
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    text = (version.stdout + version.stderr).strip()
    if f"build {pin.runtime_build}" not in text or f"commit {pin.runtime_commit[:9]}" not in text:
        raise BenchError("runtime_version_mismatch", exit_code=6)
    line = next((item for item in text.splitlines() if item.startswith("version:")), text[:200])
    return ModelIdentity(
        pin.file_name, pin.weights_sha256, pin.source, pin.chat_template_sha256, pin.binary_sha256, line
    )


def server_argv(binary: Path, weights: Path, template: Path, runtime: Runtime, port: int) -> list[str]:
    return [
        str(binary), "--model", str(weights), "--alias", "medqa-proxy", "--host", "127.0.0.1",
        "--port", str(port), "--ctx-size", str(runtime.context_tokens), "--parallel", "1",
        "--cache-type-k", runtime.kv_cache_type, "--cache-type-v", runtime.kv_cache_type,
        "--threads", str(runtime.threads), "--n-gpu-layers", str(runtime.gpu_layers),
        "--jinja", "--chat-template-file", str(template), "--reasoning-format", "deepseek",
        "--offline", "--no-webui", "--log-disable",
    ]  # fmt: skip


class ModelServer:
    """Owns one llama-server child; always stopped by close(), also on error paths."""

    def __init__(self, argv: list[str], base_url: str, runtime: Runtime, log_path: Path) -> None:
        self.base_url, self.runtime = base_url, runtime
        self._log = log_path.open("wb")
        self.process = subprocess.Popen(argv, stdout=self._log, stderr=subprocess.STDOUT)

    def wait_ready(self) -> None:
        deadline = time.monotonic() + self.runtime.load_timeout_s
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise BenchError("model_unavailable", detail="server exited", exit_code=6)
            try:
                if httpx.get(self.base_url + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(self.runtime.health_poll_s)
        raise BenchError("model_load_timeout", exit_code=6)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)
        self._log.close()


@dataclass(frozen=True, slots=True)
class Reply:
    content: str | None
    timed_out: bool
    seconds: float


class ChatClient:
    def __init__(
        self, base_url: str, generation: Generation, *, connect_timeout_s: float, max_chars: int
    ) -> None:
        self.base_url, self.generation, self.max_chars = base_url, generation, max_chars
        self.timeout = httpx.Timeout(generation.item_timeout_s, connect=connect_timeout_s)

    def body(self, prompt: str) -> dict[str, object]:
        g = self.generation
        return {
            "messages": [{"role": "system", "content": g.system}, {"role": "user", "content": prompt}],
            "temperature": g.temperature,
            "top_k": g.top_k,
            "seed": g.seed,
            "max_tokens": g.max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": g.enable_thinking},
        }

    def ask(self, prompt: str) -> Reply:
        """Timeout is a scored failure; a refused connection or a server error aborts the run."""
        started = time.monotonic()
        try:
            response = httpx.post(
                self.base_url + "/v1/chat/completions", json=self.body(prompt), timeout=self.timeout
            )
        except httpx.TimeoutException:
            return Reply(None, True, time.monotonic() - started)
        except httpx.HTTPError as exc:
            raise BenchError("model_unavailable", detail=type(exc).__name__, exit_code=6) from exc
        if response.status_code != 200:
            raise BenchError("model_unavailable", detail=str(response.status_code), exit_code=6)
        return Reply(self._content(response), False, time.monotonic() - started)

    def _content(self, response: httpx.Response) -> str | None:
        try:
            document = response.json()
            content = document["choices"][0]["message"]["content"]
        except ValueError, KeyError, IndexError, TypeError:
            return None
        return content[: self.max_chars] if isinstance(content, str) else None
