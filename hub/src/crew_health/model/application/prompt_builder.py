from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Protocol, cast

from crew_health.kernel.commands import canonical_json
from crew_health.model.application.ports import (
    ContextSnapshot,
    DecodingSettings,
    JsonValue,
    ModelReleaseReader,
    Phase,
    PromptBundle,
    TranscriptItem,
)
from crew_health.model.application.ports import (
    TokenCounter as TokenCounter,
)
from crew_health.model.domain.release import ModelRefused, object_value


class PromptAssets(Protocol):
    def system(self, version: str, phase: str) -> str: ...
    def examples(self, version: str, phase: str) -> Sequence[TranscriptItem]: ...
    def schema(self, phase: str) -> Mapping[str, JsonValue]: ...


def request_body(bundle: PromptBundle) -> dict[str, object]:
    decoding = asdict(bundle.decoding)
    body: dict[str, object] = {
        "messages": [{k: v for k, v in asdict(m).items() if v is not None} for m in bundle.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": decoding["temperature"],
        "top_p": decoding["top_p"],
        "top_k": decoding["top_k"],
        "min_p": decoding["min_p"],
        "seed": decoding["seed"],
        "max_tokens": decoding["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_format": "deepseek",
    }
    if bundle.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in bundle.tools
        ]
        body["tool_choice"] = "auto"
    if bundle.response_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": bundle.output_schema_id,
                "strict": True,
                "schema": bundle.response_schema,
            },
        }
    return body


def request_bytes(bundle: PromptBundle, release_id: str) -> bytes:
    body = request_body(bundle)
    body["model"] = release_id
    return canonical_json(body)


class PromptBuilder:
    def __init__(self, releases: ModelReleaseReader, assets: PromptAssets, tokens: TokenCounter) -> None:
        self.releases, self.assets, self.tokens = releases, assets, tokens

    def build(
        self, release_id: str, phase: Phase, context: ContextSnapshot, transcript: Sequence[TranscriptItem]
    ) -> PromptBundle:
        release = self.releases.get(release_id).document
        decoding = object_value(release["decoding"])
        maxima = object_value(decoding["max_tokens_by_phase"])
        settings = object_value(release["context_settings"])
        limit = cast(int, object_value(settings["budget_tokens_by_phase"])[phase])
        count = cast(int, settings["transcript_max_messages"])
        if len(transcript) > count:
            raise ModelRefused("prompt_too_large")
        # The E12 context is already field-authorized. JSON remains data; no content is evaluated here.
        context_text = json.dumps(
            dict(context.sections), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        messages = (
            TranscriptItem("system", self.assets.system(str(release["prompt_version"]), phase)),
            TranscriptItem(
                "user", json.dumps({"untrusted_context": json.loads(context_text)}, sort_keys=True)
            ),
            *self.assets.examples(str(release["prompt_version"]), phase),
            *transcript,
        )
        catalog = self.releases.tool_catalog(release_id)
        names = catalog.exposure.get(phase, ())
        tools = tuple(t for t in catalog.tools if t["name"] in names) or None
        sampling = DecodingSettings(
            float(cast(float, decoding["temperature"])),
            float(cast(float, decoding["top_p"])),
            int(cast(int, decoding["top_k"])),
            float(cast(float, decoding["min_p"])),
            int(cast(int, decoding["seed"])),
            int(cast(int, maxima[phase])),
            str(decoding["reasoning_format"]),
        )
        schema = self.assets.schema(phase)
        bundle = PromptBundle(
            messages,
            tools,
            "e17_" + phase,
            sampling,
            "",
            schema if release["tool_call_mode"] == "envelope" else None,
            schema,
        )
        raw = request_bytes(bundle, release_id)
        if self.tokens.count_request(request_body(bundle)) + sampling.max_tokens > limit:
            raise ModelRefused("prompt_too_large")
        return PromptBundle(
            messages,
            tools,
            bundle.output_schema_id,
            sampling,
            hashlib.sha256(raw).hexdigest(),
            bundle.response_schema,
            bundle.output_validation_schema,
        )
