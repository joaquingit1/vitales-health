"""Hub settings of the medicines module (E32 11; SYSTEM 5.11): every bound named, with a basis and a ceiling."""

from __future__ import annotations

from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.medicines.application.use_cases import Limits

# key, minimum, maximum, basis
_INTEGERS: tuple[tuple[str, int, int, str], ...] = (
    (
        "medicines.max_window_rows",
        1,
        100_000,
        "E32 3.6 team default 1000: administrations one limit window may hold before history_too_large",
    ),
    (
        "medicines.future_tolerance_seconds",
        0,
        3600,
        "SYSTEM C6 team default 300: how far a client or administered time may lead the hub clock",
    ),
    (
        "medicines.clock_backward_tolerance_seconds",
        0,
        3600,
        "SYSTEM C2 team default 60: a stored medicines time this far past the hub clock is a discontinuity",
    ),
)
CONFIG_DIR = "medicines.config_dir"


def specs() -> tuple[SettingSpec, ...]:
    integers = tuple(
        SettingSpec(
            key=key,
            kind="integer",
            unit=None,
            minimum=Decimal(low),
            maximum=Decimal(high),
            max_text_bytes=None,
            basis=basis,
        )
        for key, low, high, basis in _INTEGERS
    )
    directory = SettingSpec(
        key=CONFIG_DIR,
        kind="text",
        unit=None,
        minimum=None,
        maximum=None,
        max_text_bytes=4096,
        basis="interim until ConfigName carries treatments and medicines: the configuration directory read at start",
    )
    return (*integers, directory)


def read_limits(settings: HubSettings) -> Limits:
    return Limits(
        max_rows=settings.integer("storage.max_rows"),
        max_window_rows=settings.integer("medicines.max_window_rows"),
        future_tolerance_seconds=settings.integer("medicines.future_tolerance_seconds"),
        clock_backward_tolerance_seconds=settings.integer("medicines.clock_backward_tolerance_seconds"),
    )
