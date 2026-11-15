"""Tokenize with the identity-checked runtime's own tokenizer and chat template."""

from __future__ import annotations

import http.client
import json
from collections.abc import Mapping

from crew_health.model.domain.release import ModelRefused, object_value, strict_json


class RuntimeTokenCounter:
    def __init__(self, *, port: int, key: str, max_bytes: int, timeout_s: float) -> None:
        self.port, self.key, self.max_bytes, self.timeout_s = port, key, max_bytes, timeout_s

    def count(self, text: str) -> int:
        value = self._post("/tokenize", {"content": text, "add_special": False, "parse_special": True})
        tokens = value.get("tokens")
        if not isinstance(tokens, list) or any(isinstance(t, bool) or not isinstance(t, int) for t in tokens):
            raise ModelRefused("tokenizer_invalid")
        return len(tokens)

    def count_request(self, body: Mapping[str, object]) -> int:
        # /apply-template and chat/completions share the pinned native template implementation.
        value = self._post("/apply-template", dict(body))
        prompt = value.get("prompt")
        if not isinstance(prompt, str):
            raise ModelRefused("tokenizer_invalid")
        return self.count(prompt)

    def _post(self, path: str, body: Mapping[str, object]) -> dict[str, object]:
        raw = json.dumps(body, allow_nan=False).encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ModelRefused("prompt_too_large")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout_s)
        try:
            connection.request(
                "POST", path, raw, {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ModelRefused("tokenizer_unavailable")
            return object_value(strict_json(response.read(self.max_bytes + 1), max_bytes=self.max_bytes))
        except (OSError, http.client.HTTPException) as error:
            raise ModelRefused("tokenizer_unavailable") from error
        finally:
            connection.close()
