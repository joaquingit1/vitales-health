"""Canonical text forms shared by the repository and the representation (UTC instants, decimal quantities)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crew_health.measurement.application.errors import StoredInvariant


def format_instant(value: datetime) -> str:
    if value.utcoffset() != timedelta(0):
        raise StoredInvariant("instant_must_be_utc")
    utc = value.astimezone(UTC)
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(text: str) -> datetime:
    if not text.endswith("Z"):
        raise StoredInvariant("stored_instant_must_end_in_z")
    value = datetime.fromisoformat(text[:-1] + "+00:00")
    return value.astimezone(UTC)
