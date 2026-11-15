from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.documents import BenchSettings, decode_json
from crew_health_bench.domain.documents import integer, number, object_map, text
from crew_health_bench.domain.errors import BenchError


class GraderHTTP:
    def __init__(self, *, client: httpx.Client, base_url: str, grader: Mapping[str, object],
                 environment: Mapping[str, str], settings: BenchSettings, sleep: Callable[[Quantity], None]) -> None:
        url = urlsplit(base_url)
        try:
            local = url.hostname is not None and ip_address(url.hostname).is_loopback
        except ValueError:
            local = False
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.query or url.fragment or (not local and url.scheme != "https")):
            raise BenchError("grader_unavailable")
        self.client, self.url = client, base_url.rstrip("/") + "/v1/chat/completions"
        self.key = environment.get(text(grader["api_key_env"]))
        self.grader, self.settings, self.sleep = grader, settings, sleep
        self.calls = 0
        self.errors = 0

    def _once(self, messages: tuple[tuple[str, str], ...], timeout: Quantity) -> str:
        if not self.key:
            raise BenchError("grader_unavailable")
        body = {"model": text(self.grader["model"]), "temperature": float(number(self.grader["temperature"])),
                "max_tokens": integer(self.grader["max_tokens"]),
                "messages": [{"role": "system", "content": text(self.grader["system_message"])},
                             *({"role": role, "content": content} for role, content in messages)]}
        self.calls += 1
        maximum = self.settings.integer("bench.store.max_line_bytes")
        with self.client.stream("POST", self.url, json=body, headers={"Authorization": "Bearer " + self.key},
                                timeout=float(timeout.value), follow_redirects=False) as response:
            if response.status_code != 200:
                raise GraderStatus(response.status_code)
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=maximum):
                if len(raw) + len(chunk) > maximum:
                    raise BenchError("grader_unavailable")
                raw.extend(chunk)
        row = decode_json(bytes(raw))
        choices = row.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise BenchError("grader_unavailable")
        content = object_map(object_map(choices[0]).get("message")).get("content")
        if not isinstance(content, str):
            raise BenchError("grader_unavailable")
        return content

    def complete(self, messages: tuple[tuple[str, str], ...], timeout: Quantity) -> str:
        settings = self.settings
        if timeout.unit.code != "s" or timeout.value <= 0:
            raise BenchError("grader_unavailable")
        retries = settings.integer("bench.grader.max_retries")
        initial = settings.quantity("bench.grader.backoff_initial")
        cap = settings.quantity("bench.grader.backoff_cap")
        if not self.key:
            raise BenchError("grader_unavailable")
        for attempt in range(1 + retries):
            try:
                return self._once(messages, timeout)
            except GraderStatus as error:
                self.errors += 1
                if error.status not in (429, 503) or attempt == retries:
                    raise BenchError("grader_unavailable") from error
            except httpx.HTTPError as error:
                self.errors += 1
                if attempt == retries:
                    raise BenchError("grader_unavailable") from error
            self.sleep(Quantity(min(initial.value * Decimal(2) ** attempt, cap.value), Unit("s")))
        raise BenchError("grader_unavailable")


class GraderStatus(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__("grader_unavailable")
