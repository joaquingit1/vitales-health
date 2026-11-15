from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.units import Quantity, Unit
from crew_health.platform.domain.errors import fail


class ValidatedSettings:
    def __init__(self, specs: Sequence[SettingSpec], values: Mapping[str, object]) -> None:
        self._specs = {spec.key: spec for spec in specs}
        self._values = dict(values)
        if len(self._specs) != len(specs) or self._specs.keys() != self._values.keys():
            raise fail(ErrorCode.CONFIG_INVALID)
        for key, spec in self._specs.items():
            value = self._values[key]
            if spec.kind == "integer" and type(value) is int:
                self._range(spec, Decimal(value))
            elif spec.kind == "quantity" and isinstance(value, Quantity) and value.unit == spec.unit:
                self._range(spec, value.value)
            elif spec.kind == "boolean" and type(value) is bool:
                continue
            elif spec.kind == "text" and isinstance(value, str) and spec.max_text_bytes is not None:
                if len(value.encode()) > spec.max_text_bytes:
                    raise fail(ErrorCode.CONFIG_INVALID)
            else:
                raise fail(ErrorCode.CONFIG_INVALID)

    @classmethod
    def from_json(cls, raw: bytes, *, specs: Sequence[SettingSpec], max_bytes: int) -> ValidatedSettings:
        if not 1 <= len(raw) <= max_bytes:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        try:
            document: object = json.loads(
                raw, object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=_invalid_constant
            )
            data = _object(document)
            if set(data) != {"settings"} or not isinstance(data["settings"], list):
                raise ValueError("settings document")
            entries = cast(list[object], data["settings"])
            if len(entries) != len(specs):
                raise ValueError("settings inventory")
            registered = {spec.key: spec for spec in specs}
            active: list[SettingSpec] = []
            values: dict[str, object] = {}
            for entry in entries:
                spec, value = _entry(entry, registered)
                if spec.key in values:
                    raise ValueError("duplicate setting")
                active.append(spec)
                values[spec.key] = value
            return cls(active, values)
        except ValueError, TypeError, UnicodeError, RecursionError, ArithmeticError:
            raise fail(ErrorCode.CONFIG_INVALID) from None

    def _range(self, spec: SettingSpec, value: Decimal) -> None:
        if (
            not value.is_finite()
            or spec.minimum is None
            or spec.maximum is None
            or not spec.minimum <= value <= spec.maximum
        ):
            raise fail(ErrorCode.CONFIG_INVALID)

    def integer(self, key: str) -> int:
        value = self._values.get(key)
        if type(value) is not int:
            raise fail(ErrorCode.CONFIG_INVALID)
        return value

    def quantity(self, key: str) -> Quantity:
        value = self._values.get(key)
        if not isinstance(value, Quantity):
            raise fail(ErrorCode.CONFIG_INVALID)
        return value

    def boolean(self, key: str) -> bool:
        value = self._values.get(key)
        if type(value) is not bool:
            raise fail(ErrorCode.CONFIG_INVALID)
        return value

    def text(self, key: str) -> str:
        value = self._values.get(key)
        if not isinstance(value, str):
            raise fail(ErrorCode.CONFIG_INVALID)
        return value


def specifications() -> tuple[SettingSpec, ...]:
    bounds = (
        ("storage.busy_timeout_ms", 1, 2000),
        ("storage.max_rows", 1, 1000),
        ("storage.max_vm_steps", 1, 10000000),
        ("storage.max_statement_bytes", 1, 1048576),
        ("storage.reserve_mb", 1, 256),
        ("storage.writer_queue_max", 1, 64),
        ("storage.transaction_timeout_ms", 1, 2000),
        ("event_bus.max_events", 1, 1024),
        ("commands.max_bytes", 1, 8388608),
        ("commands.cache_entries", 1, 10000),
        ("commands.max_depth", 1, 64),
        ("commands.multipart_overhead_bytes", 1, 1048576),
        ("private.max_entry_bytes", 20000000, 33554432),
        ("private.recovery_batch", 1, 200),
        ("private.max_report_bytes", 20000000, 20000000),
        ("private.max_versions", 1, 10000),
        ("private.coordinator_timeout_ms", 1, 300000),
        ("scheduler.batch_size", 1, 200),
        ("scheduler.poll_ms", 1, 1000),
        ("scheduler.max_jobs", 1, 100000),
        ("scheduler.retry_max", 0, 10),
        ("scheduler.retry_backoff_ms", 1, 60000),
        ("tasks.max_pending", 1, 10000),
        ("tasks.claim_batch", 1, 200),
        ("tasks.max_attempts", 1, 10),
        ("tasks.lease_ms", 1, 300000),
        ("tasks.retry_backoff_ms", 1, 60000),
        ("tasks.max_input_bytes", 1, 1048576),
        ("tasks.max_result_bytes", 1, 1048576),
        ("evaluation.retry_max", 0, 10),
        ("evaluation.retry_backoff_ms", 1, 60000),
        ("evaluation.page_size", 1, 200),
        ("sync.page_entries", 1, 200),
        ("sync.pending_max", 1, 10000),
        ("sync.max_bytes", 1, 8388608),
        ("sync.retry_max", 0, 10),
        ("sync.timeout_ms", 1, 30000),
        ("sync.transfer_timeout_ms", 1, 300000),
        ("sync.max_projection_fields", 1, 1024),
        ("sync.max_dependencies", 1, 256),
        ("registry.max_modules", 1, 128),
        ("registry.max_ports", 1, 1024),
        ("registry.max_hooks", 1, 1024),
        ("registry.startup_timeout_ms", 1, 300000),
        ("backup.timeout_ms", 1, 300000),
        ("workers.hub.timeout_ms", 1, 300000),
        ("workers.shutdown_timeout_ms", 1, 300000),
        ("supervisor.poll_ms", 1, 60000),
        ("supervisor.subscriber_pages", 1, 64),
        ("units.max_cached", 1, 4096),
    )
    return tuple(
        SettingSpec(
            key,
            "integer",
            None,
            Decimal(low),
            Decimal(high),
            None,
            "SYSTEM 5.11; E01 section 11 operational ceiling",
        )
        for key, low, high in bounds
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate property")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError("nonfinite number")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("object required")
    return cast(dict[str, object], value)


def _entry(value: object, registered: Mapping[str, SettingSpec]) -> tuple[SettingSpec, object]:
    entry = _object(value)
    if set(entry) != {"key", "kind", "unit", "minimum", "maximum", "basis", "value"}:
        raise ValueError("setting fields")
    key, basis = entry["key"], entry["basis"]
    if not isinstance(key, str) or key not in registered or not isinstance(basis, str) or not basis:
        raise ValueError("setting identity")
    original = registered[key]
    unit = original.unit.code if original.unit else None
    if entry["kind"] != original.kind or entry["unit"] != unit:
        raise ValueError("setting kind or unit")
    minimum = _bound(entry["minimum"], original.minimum, lower=True)
    maximum = _bound(entry["maximum"], original.maximum, lower=False)
    spec = SettingSpec(key, original.kind, original.unit, minimum, maximum, original.max_text_bytes, basis)
    content = entry["value"]
    if original.kind == "quantity":
        quantity = _object(content)
        if (
            set(quantity) != {"value", "unit"}
            or not isinstance(quantity["value"], str)
            or quantity["unit"] != unit
            or not isinstance(unit, str)
        ):
            raise ValueError("quantity setting")
        content = Quantity(Decimal(quantity["value"]), Unit(unit))
    return spec, content


def _bound(value: object, original: Decimal | None, *, lower: bool) -> Decimal | None:
    if original is None:
        if value is not None:
            raise ValueError("unexpected bound")
        return None
    if not isinstance(value, str):
        raise ValueError("bound must be decimal text")
    parsed = Decimal(value)
    if not parsed.is_finite() or lower and parsed < original or not lower and parsed > original:
        raise ValueError("bound weakens registration")
    return parsed
