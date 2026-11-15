"""Explicit deterministic real SQLite test station. Never a production fallback."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from crew_health.kernel.clock import Clock
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemoryRelease
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase


def test_settings() -> ValidatedSettings:
    values = {
        "storage.busy_timeout_ms": 100,
        "storage.max_rows": 1000,
        "storage.max_vm_steps": 1000000,
        "storage.max_statement_bytes": 1048576,
        "storage.writer_queue_max": 8,
        "storage.transaction_timeout_ms": 2000,
        "storage.reserve_mb": 1,
        "backup.timeout_ms": 1000,
        "event_bus.max_events": 100,
        "events.max_subscribers": 128,
        "events.page_size": 50,
        "events.retention_hours": 24,
        "events.max_streams": 4,
        "events.stream_queue_size": 16,
        "events.heartbeat_seconds": 15,
        "scheduler.max_jobs": 10000,
        "scheduler.poll_ms": 100,
        "scheduler.batch_size": 50,
        "scheduler.retry_max": 3,
        "scheduler.retry_backoff_ms": 100,
        "tasks.max_pending": 100,
        "tasks.claim_batch": 20,
        "tasks.max_attempts": 3,
        "tasks.lease_ms": 1000,
        "tasks.max_input_bytes": 1048576,
        "tasks.max_result_bytes": 1048576,
        "tasks.retry_backoff_ms": 100,
        "evaluation.page_size": 50,
        "evaluation.retry_max": 3,
        "evaluation.retry_backoff_ms": 100,
        "private.max_entry_bytes": 33554432,
        "private.recovery_batch": 100,
        "private.max_report_bytes": 20000000,
        "workers.hub.timeout_ms": 2000,
        "workers.shutdown_timeout_ms": 2000,
        "supervisor.poll_ms": 100,
        "supervisor.subscriber_pages": 4,
        "units.max_cached": 64,
        "private.max_versions": 10000,
        "private.coordinator_timeout_ms": 1000,
        "sync.pending_max": 1000,
        "sync.retry_max": 3,
        "sync.timeout_ms": 1000,
        "sync.transfer_timeout_ms": 10000,
        "text_rules.max_text_bytes": 8388608,
        "text_rules.max_matches": 10000,
        "records.query_max_ids": 1000,
        "records.query_max_codes": 200,
        "records.query_max_rows": 1000,
        "records.query_max_window_seconds": 315360000,
        "sync.page_entries": 100,
        "sync.max_bytes": 8388608,
        "sync.max_projection_fields": 1024,
        "sync.max_dependencies": 256,
        "commands.max_bytes": 8388608,
        "commands.max_depth": 64,
        "commands.cache_entries": 10000,
        "commands.multipart_overhead_bytes": 1048576,
        "registry.max_hooks": 1024,
        "registry.max_modules": 128,
        "registry.max_ports": 1024,
        "registry.startup_timeout_ms": 300000,
        "clock.regression_tolerance_ms": 0,
        "clock.future_tolerance_ms": 1000,
        "clock.max_uncertainty_ms": 60000,
        "clock.hlc_future_ms": 1000,
        "clock.hlc_logical_max": 2147483647,
        "clock.history_page_size": 100,
    }
    specs = tuple(
        SettingSpec(
            k,
            "integer",
            None,
            Decimal(0),
            Decimal(max(v, 1)),
            None,
            "Explicit hermetic operational test profile",
        )
        for k, v in values.items()
    )
    return ValidatedSettings(specs, values)


def database(directory: Path, *, clock: Clock | None = None, initialize: bool = True) -> SQLiteDatabase:
    ids = MemoryIds(identifiers=tuple(str(i) for i in range(10000)))
    return SQLiteDatabase(
        directory / "hub.sqlite",
        clock=clock or MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_1"),
        ids=ids,
        release=MemoryRelease("release_1", {}),
        settings=test_settings(),
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=initialize,
    )
