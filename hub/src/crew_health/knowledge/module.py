from __future__ import annotations

from decimal import Decimal

from crew_health.app.registry import ModuleRegistry
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.units import Unit
from crew_health.knowledge.api.passages import create_router


def _integers() -> tuple[tuple[str, int], ...]:
    return (
        ("knowledge.registry.max_rows", 100000),
        ("knowledge.bundle.max_members", 5000),
        ("knowledge.bundle.max_json_depth", 16),
        ("knowledge.query.max_source_ids", 200),
        ("knowledge.review.max_records", 200000),
        ("knowledge.review.max_chain_depth", 1000),
        ("knowledge.bundle.max_records", 200000),
        ("knowledge.passage.max_chars", 8000),
        ("knowledge.vectors.max_dims", 4096),
        ("knowledge.query.max_chars", 1000),
        ("knowledge.query.k_max", 50),
        ("knowledge.query.limit_max", 200),
        ("knowledge.query.expand_neighbors_max", 3),
        ("knowledge.forms.max_pages", 1000),
        ("knowledge.catalog.page_size", 200),
        ("knowledge.catalog.max_rows", 1000000),
        ("knowledge.archive.max_passages", 1000000),
        ("knowledge.archive.max_memberships", 4000000),
        ("knowledge.readers.max_open", 3),
        ("knowledge.cache.max_entries", 1000),
        ("knowledge.retention.keep_indexes", 5),
        ("knowledge.admin.max_pending", 8),
        ("knowledge.admin.batch_size", 200),
        ("knowledge.embedding.build_retries", 2),
        ("knowledge.embedding.batch_size", 64),
    )


def _bytes() -> tuple[tuple[str, int], ...]:
    return (
        ("knowledge.bundle.max_member_bytes", 268435456),
        ("knowledge.bundle.max_manifest_bytes", 1048576),
        ("knowledge.bundle.max_total_expanded_bytes", 1073741824),
        ("knowledge.bundle.max_line_bytes", 262144),
        ("knowledge.bundle.max_compressed_bytes", 536870912),
        ("knowledge.capture.max_bytes", 268435456),
        ("knowledge.io.chunk_bytes", 65536),
        ("knowledge.forms.max_page_bytes", 262144),
        ("knowledge.archive.max_bytes", 10737418240),
        ("knowledge.readers.mmap_bytes", 67108864),
        ("knowledge.install.rss_bytes", 536870912),
        ("knowledge.install.min_free_bytes", 1073741824),
    )


def _durations() -> tuple[tuple[str, int], ...]:
    return (
        ("knowledge.query.timeout_ms", 250),
        ("knowledge.registry.lookup_timeout_ms", 10),
        ("knowledge.install.deadline_ms", 300000),
        ("knowledge.install.cancel_timeout_ms", 2000),
        ("knowledge.embedding.query_timeout_ms", 2000),
        ("knowledge.embedding.build_timeout_ms", 30000),
        ("knowledge.embedding.retry_backoff_ms", 8000),
        ("knowledge.embedding.acquire_timeout_ms", 2000),
    )


def setting_specs() -> tuple[SettingSpec, ...]:
    basis = "docs/tdd/E10.md section 11; operational ceilings pending reference laptop measurement"
    return (
        *(
            SettingSpec(
                key,
                "integer",
                None,
                Decimal(
                    0
                    if key in ("knowledge.query.expand_neighbors_max", "knowledge.embedding.build_retries")
                    else 1
                ),
                Decimal(maximum),
                None,
                basis,
            )
            for key, maximum in _integers()
        ),
        *(
            SettingSpec(key, "quantity", Unit("By"), Decimal(1), Decimal(maximum), None, basis)
            for key, maximum in _bytes()
        ),
        *(
            SettingSpec(key, "quantity", Unit("ms"), Decimal(1), Decimal(maximum), None, basis)
            for key, maximum in _durations()
        ),
    )


def register(registry: ModuleRegistry) -> None:
    registry.router(create_router())
    for prefix in ("cat", "dv", "ex", "psg", "evr", "lic"):
        registry.id_prefix(record_type=prefix, prefix=prefix)
    for spec in setting_specs():
        registry.setting(spec)
