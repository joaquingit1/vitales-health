"""E02 11 consent bounds, registered through S5.11 with basis `team default`."""

from __future__ import annotations

from decimal import Decimal

from crew_health.consent.application.service import ACTIVE_CEILING, FIELDS_CEILING, ConsentLimits
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.settings import HubSettings, SettingSpec

BASIS = "team default"
CONSENT_SETTINGS: tuple[tuple[str, int, int, int], ...] = (
    ("consent.active_per_subject_max", 1, ACTIVE_CEILING, 64),
    ("consent.fields_max", 1, FIELDS_CEILING, 64),
)


def specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, BASIS)
        for key, low, high, _ in CONSENT_SETTINGS
    )


def defaults() -> dict[str, int]:
    return {key: default for key, _, _, default in CONSENT_SETTINGS}


def limits(settings: HubSettings) -> ConsentLimits:
    """Refuses to start when storage.max_rows cannot hold one complete read of a subject's active grants."""
    if settings.integer("storage.max_rows") <= ACTIVE_CEILING:
        raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
    return ConsentLimits(
        settings.integer("consent.active_per_subject_max"), settings.integer("consent.fields_max")
    )
