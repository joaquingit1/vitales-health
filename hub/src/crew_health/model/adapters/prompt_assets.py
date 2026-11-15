from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from crew_health.model.adapters.manifest_store import ManifestStore, child
from crew_health.model.application.ports import JsonValue, TranscriptItem
from crew_health.model.domain.release import ModelRefused, strict_json


class FilePromptAssets:
    def __init__(self, store: ManifestStore) -> None:
        self.store = store

    def system(self, version: str, phase: str) -> str:
        path = child(self.store.root / "prompts", version) / ("system." + phase + ".md")
        with path.open("r", encoding="utf-8") as stream:
            text = stream.read(self.store.max_bytes + 1)
        if len(text.encode()) > self.store.max_bytes:
            raise ModelRefused("prompt_too_large")
        return text

    def examples(self, version: str, phase: str) -> Sequence[TranscriptItem]:
        # Version 1 deliberately carries no few-shot examples; absence is explicit, never sample fallback.
        path = child(self.store.root / "prompts", version) / ("examples." + phase + ".json")
        with path.open("rb") as stream:
            value = strict_json(stream.read(self.store.max_bytes + 1), max_bytes=self.store.max_bytes)
        if value != []:
            raise ModelRefused("examples_require_dev_leak_scan")
        return ()

    def schema(self, phase: str) -> Mapping[str, JsonValue]:
        name = (
            "insight-draft"
            if phase == "insight"
            else "claim-support"
            if phase == "verifier"
            else "turn-output"
        )
        return cast(
            Mapping[str, JsonValue],
            self.store.read_json(self.store.root / "schemas" / (name + ".schema.json")),
        )
