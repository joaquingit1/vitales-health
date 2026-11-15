from collections.abc import Sequence
from ipaddress import ip_address
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
from crew_health.kernel.units import Quantity

from crew_health_bench.adapters.documents import BenchSettings, decode_json
from crew_health_bench.domain.documents import integer, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.model import (
    ChatMessage,
    Completion,
    DecodingSettings,
    EndpointIdentity,
    ModelReleaseRef,
)


class LlamaEndpoint:
    def __init__(self, base_url: str, settings: BenchSettings, client: httpx.Client) -> None:
        parsed = urlsplit(base_url)
        try:
            local = parsed.hostname is not None and ip_address(parsed.hostname).is_loopback
        except ValueError as error:
            raise BenchError("live_hub_refused", exit_code=5) from error
        if (
            not local
            or parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise BenchError("live_hub_refused", exit_code=5)
        self.base_url, self.client = base_url.rstrip("/"), client
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.connect_timeout = settings.quantity("bench.model.connect_timeout")

    def _call(
        self, method: str, path: str, body: dict[str, object] | None, timeout: Quantity
    ) -> dict[str, object]:
        if timeout.unit.code != "s" or timeout.value <= 0:
            raise BenchError("case_invalid")
        bounded = httpx.Timeout(float(timeout.value), connect=float(self.connect_timeout.value))
        try:
            with self.client.stream(
                method, self.base_url + path, json=body, timeout=bounded, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise BenchError("model_unavailable")
                data = bytearray()
                for part in response.iter_bytes(chunk_size=self.maximum):
                    if len(data) + len(part) > self.maximum:
                        raise BenchError("model_response_bound", exit_code=6)
                    data.extend(part)
                return decode_json(bytes(data))
        except httpx.TimeoutException as error:
            raise BenchError("timeout") from error
        except httpx.HTTPError as error:
            raise BenchError("model_unavailable") from error

    def identity(self, *, timeout: Quantity) -> EndpointIdentity:
        row = self._call("GET", "/props", None, timeout)
        return EndpointIdentity(
            text(row.get("model_path")),
            integer(object_map(row.get("default_generation_settings")).get("n_ctx")),
        )

    def require_identity(self, release: ModelReleaseRef, *, timeout: Quantity) -> None:
        identity = self.identity(timeout=timeout)
        if (
            PurePosixPath(identity.model_path.replace("\\", "/")).name != release.weights_sha256 + ".gguf"
            or identity.context_tokens != release.context_tokens
        ):
            raise BenchError("model_identity_mismatch")

    def complete(
        self, messages: Sequence[ChatMessage], settings: DecodingSettings, *, timeout: Quantity
    ) -> Completion:
        if not messages:
            raise BenchError("case_invalid")
        body = {
            **settings.request(),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        row = self._call("POST", "/v1/chat/completions", body, timeout)
        choices = row.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise BenchError("model_response_invalid")
        choice = object_map(choices[0])
        message = object_map(choice.get("message"))
        usage = {
            name: integer(value)
            for name, value in object_map(row.get("usage")).items()
            if name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        if any(value < 0 for value in usage.values()):
            raise BenchError("model_response_invalid")
        return Completion(text(message.get("content")), None, usage, text(choice.get("finish_reason")))
