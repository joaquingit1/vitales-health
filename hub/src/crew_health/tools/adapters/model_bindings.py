"""E12's side of E17's model release seam (E12 4.2 and 4.4; E17 ModelReleaseReader and TokenCounter).

The tool catalog, retrieval settings and tokenizer come from the pinned model release, never from E12 defaults.
A release whose rows do not compile, or settings that do not parse exactly, leave the dependent tools unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import jsonschema

from crew_health.model.application.ports import ModelRelease, ModelReleaseReader
from crew_health.model.application.ports import TokenCounter as ModelTokenCounter
from crew_health.tools.adapters.jsonschema_validator import CatalogValidator, CompiledCatalog
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.retrieval import RetrievalSettings

_RETRIEVAL_KEYS = frozenset(
    {"k_lexical", "k_dense", "final_min", "final_max", "budget_tokens", "max_per_parent", "k_rrf", "rerank"}
)


def _integer(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if type(value) is not int or value < 0:
        raise ToolsInvalid("CONFIG_INVALID")
    return value


def retrieval_settings(values: Mapping[str, object]) -> RetrievalSettings:
    """E17's retrieval_settings object exactly (model-release.schema.json); anything else rejects the release."""
    if set(values) != _RETRIEVAL_KEYS or type(values["rerank"]) is not bool:
        raise ToolsInvalid("CONFIG_INVALID")
    if _integer(values, "final_min") > _integer(values, "final_max"):
        raise ToolsInvalid("CONFIG_INVALID")
    return RetrievalSettings(
        k_lexical=_integer(values, "k_lexical"),
        k_dense=_integer(values, "k_dense"),
        final_max=_integer(values, "final_max"),
        budget_tokens=_integer(values, "budget_tokens"),
        max_per_parent=_integer(values, "max_per_parent"),
        k_rrf=Decimal(_integer(values, "k_rrf")),
        rerank=values["rerank"] is True,
    )


# E17 refuses unusable release artifacts with ModelRefused (a ValueError kept in its domain package, which E12 may
# not import) or with jsonschema's SchemaError from its catalog check. Either means the release cannot back a tool.
_REFUSALS = (ValueError, jsonschema.exceptions.SchemaError)


def active_release(reader: ModelReleaseReader) -> ModelRelease | None:
    """The active model release, or None when E17 refuses its artifacts: the hub serves without the model."""
    try:
        return reader.active()
    except _REFUSALS:
        return None


def compile_catalog(
    reader: ModelReleaseReader, validator: CatalogValidator, model_release_id: str
) -> CompiledCatalog:
    try:
        catalog = reader.tool_catalog(model_release_id)
    except _REFUSALS as error:
        raise ToolsInvalid("CONFIG_INVALID") from error
    return validator.compile_release(catalog.tools, catalog.exposure, model_release_id=model_release_id)


def release_retrieval(reader: ModelReleaseReader, model_release_id: str) -> RetrievalSettings:
    try:
        values = reader.retrieval_settings(model_release_id).values
    except _REFUSALS as error:
        raise ToolsInvalid("CONFIG_INVALID") from error
    return retrieval_settings(values)


class ModelTokens:
    """E12's TokenCounter over the pinned release's tokenizer. A prompt for another release has no count."""

    def __init__(self, tokens: ModelTokenCounter, model_release_id: str) -> None:
        self._tokens, self._model_release_id = tokens, model_release_id

    def count(self, prompt: bytes, *, model_release_id: str) -> int:
        if model_release_id != self._model_release_id:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        try:
            text = prompt.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED") from error
        count = self._tokens.count(text)
        if type(count) is not int or count < 0:
            raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
        return count
