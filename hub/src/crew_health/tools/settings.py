from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.units import Unit
from crew_health.tools.domain.arguments import JsonBounds
from crew_health.tools.domain.errors import ToolsInvalid


def _counts() -> tuple[tuple[str, int], ...]:
    return (
        ("tools.max_calls_per_turn", 32),
        ("tools.max_effects_per_turn", 8),
        ("tools.max_json_depth", 16),
        ("tools.max_array_items", 50),
        ("tools.max_string_chars", 2000),
        ("tools.max_handlers", 64),
        ("tools.query.max_chars", 300),
        ("tools.query.max_terms", 50),
        ("tools.query.max_filters", 50),
        ("tools.retrieval.k_max", 50),
        ("tools.retrieval.candidates_max", 150),
        ("tools.retrieval.final_max", 8),
        ("tools.retrieval.neighbors_max", 3),
        ("tools.retrieval.max_expansions", 8),
        ("tools.context.max_sections", 16),
        ("tools.context.max_items", 200),
        ("tools.context.max_tokens", 32768),
        ("tools.retrieval.max_tokens", 8192),
        ("tools.max_concurrent_calls", 1),
        ("tools.max_open_turns", 64),
        ("tools.bench.max_queries", 500),
    )


def setting_specs() -> tuple[SettingSpec, ...]:
    basis = "docs/tdd/E12.md section 11; operational ceilings pending reference measurement"
    integers = tuple(
        SettingSpec(key, "integer", None, Decimal(1), Decimal(cap), None, basis) for key, cap in _counts()
    )
    zeros = tuple(
        SettingSpec(key, "integer", None, Decimal(0), Decimal(0), None, basis)
        for key in ("tools.external_retries", "tools.private_cache_entries")
    )
    quantities = (
        ("tools.max_argument_bytes", "By", 8192),
        ("tools.max_result_bytes", "By", 262144),
        ("tools.max_schema_bytes", "By", 262144),
        ("tools.context.max_bytes", "By", 262144),
        ("tools.rss_bytes", "By", 52428800),
        ("tools.timeout_ms", "ms", 10000),
        ("tools.turn_timeout_ms", "ms", 300000),
        ("tools.rerank.timeout_ms", "ms", 2000),
        ("tools.bench.timeout_ms", "ms", 300000),
    )
    return (
        *integers,
        *zeros,
        *(
            SettingSpec(key, "integer", Unit(unit), Decimal(1), Decimal(cap), None, basis)
            for key, unit, cap in quantities
        ),
    )


def validate_settings(settings: HubSettings) -> None:
    for spec in setting_specs():
        value = settings.integer(spec.key)
        if type(value) is not int:
            raise ToolsInvalid("CONFIG_INVALID")
        amount = Decimal(value)
        if (
            not amount.is_finite()
            or amount != amount.to_integral_value()
            or spec.minimum is None
            or spec.maximum is None
            or not spec.minimum <= amount <= spec.maximum
        ):
            raise ToolsInvalid("CONFIG_INVALID")


def argument_bounds(settings: HubSettings) -> JsonBounds:
    validate_settings(settings)
    return JsonBounds(
        max_bytes=settings.integer("tools.max_argument_bytes"),
        max_depth=settings.integer("tools.max_json_depth"),
        max_array_items=settings.integer("tools.max_array_items"),
        max_string_chars=settings.integer("tools.max_string_chars"),
    )
