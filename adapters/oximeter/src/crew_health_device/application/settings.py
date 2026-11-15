"""Required operational bounds, independent from medical configuration."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SettingDescriptor:
    key: str
    unit: str | None
    minimum: int
    maximum: int
    basis: str


def descriptors() -> tuple[SettingDescriptor, ...]:
    bounds = (
        ("connect_timeout_ms", "ms", 60000),
        ("frame_timeout_ms", "ms", 60000),
        ("acquisition_window_ms", "ms", 600000),
        ("max_frames", None, 4096),
        ("max_frame_bytes", "By", 512),
        ("send_timeout_ms", "ms", 60000),
        ("journal_max_entries", None, 10000),
        ("journal_max_bytes", "By", 67108864),
        ("retry_max", None, 1000),
        ("retry_backoff_ms", "ms", 60000),
        ("health_quiet_ms", "ms", 600000),
        ("profile_max_bytes", "By", 1048576),
    )
    return tuple(
        SettingDescriptor(f"adapter.{key}", unit, 1, maximum, "E07 spec 2.2 operational bound; SYSTEM 5.11")
        for key, unit, maximum in bounds
    )


@dataclass(frozen=True, slots=True)
class AdapterSettings:
    connect_timeout_ms: int
    frame_timeout_ms: int
    acquisition_window_ms: int
    max_frames: int
    max_frame_bytes: int
    send_timeout_ms: int
    journal_max_entries: int
    journal_max_bytes: int
    retry_max: int
    retry_backoff_ms: int
    health_quiet_ms: int
    profile_max_bytes: int


class SettingsError(Exception):
    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"{key}:{reason}")


def _value(document: Mapping[str, object], descriptor: SettingDescriptor) -> int:
    entry = document.get(descriptor.key)
    if not isinstance(entry, dict):
        raise SettingsError(descriptor.key, "missing_or_invalid_entry")
    if set(entry) != {"value", "unit", "basis", "to_be_set_by"}:
        raise SettingsError(descriptor.key, "invalid_entry_fields")
    value = entry["value"]
    if type(value) is not int:
        raise SettingsError(descriptor.key, "integer_required")
    if not descriptor.minimum <= value <= descriptor.maximum:
        raise SettingsError(descriptor.key, "out_of_range")
    if entry["unit"] != descriptor.unit:
        raise SettingsError(descriptor.key, "invalid_unit")
    for field in ("basis", "to_be_set_by"):
        text = entry[field]
        if not isinstance(text, str) or not text.strip():
            raise SettingsError(descriptor.key, f"empty_{field}")
    return value


def load_settings(document: Mapping[str, object]) -> AdapterSettings:
    bounds = descriptors()
    unknown = set(document) - {item.key for item in bounds}
    if unknown:
        raise SettingsError(sorted(unknown)[0], "unknown_setting")
    return AdapterSettings(*(_value(document, item) for item in bounds))
