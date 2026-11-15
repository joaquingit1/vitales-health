"""Required clinical import operational bounds, with the shared settings error contract."""

from collections.abc import Mapping
from dataclasses import dataclass

from crew_health_device.application.settings import SettingDescriptor, SettingsError


def descriptors() -> tuple[SettingDescriptor, ...]:
    return tuple(
        SettingDescriptor(f"import.{key}", unit, 1, maximum, "E07-02 input bounds; SYSTEM 5.11")
        for key, unit, maximum in (
            ("max_bytes", "By", 10485760),
            ("max_entries", None, 10000),
            ("max_depth", None, 64),
            ("max_text_bytes", "By", 65536),
        )
    )


@dataclass(frozen=True, slots=True)
class ImportSettings:
    max_bytes: int
    max_entries: int
    max_depth: int
    max_text_bytes: int

    def __post_init__(self) -> None:
        for descriptor in descriptors():
            value = getattr(self, descriptor.key.removeprefix("import."))
            if type(value) is not int:
                raise SettingsError(descriptor.key, "integer_required")
            if not descriptor.minimum <= value <= descriptor.maximum:
                raise SettingsError(descriptor.key, "out_of_range")


def _value(document: Mapping[str, object], descriptor: SettingDescriptor) -> int:
    # The shared implementation helper is private. Mirror its checks for import keys only.
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


def load_settings(document: Mapping[str, object]) -> ImportSettings:
    bounds = descriptors()
    unknown = set(document) - {item.key for item in bounds}
    if unknown:
        raise SettingsError(sorted(unknown)[0], "unknown_setting")
    return ImportSettings(*(_value(document, descriptor) for descriptor in bounds))
