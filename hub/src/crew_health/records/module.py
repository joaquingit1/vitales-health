from __future__ import annotations

from typing import cast

from crew_health.app.registry import ModuleRegistry
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.ports import RecordsStore
from crew_health.records.settings import specs


def register(registry: ModuleRegistry) -> None:
    registry.provide(cast(type[object], RecordsStore), lambda resolver: RecordRepository())
    registry.id_prefix(record_type="observation", prefix="obs")
    registry.id_prefix(record_type="answer", prefix="ans")
    for spec in specs():
        registry.setting(spec)
