"""CONTRACTS 12: pure schedule and demo resolution."""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.groundlink.domain.errors import DemoSwitchInvalid

type LinkStatus = Literal["up", "delayed", "down"]
type GapKind = Literal["planned_line_of_sight", "relay_outage", "conjunction", "equipment", "unknown"]


@dataclass(frozen=True, slots=True)
class ScheduleWindow:
    starts_at: datetime
    ends_at: datetime
    status: LinkStatus
    gap_kind: GapKind | None
    planned: bool


@dataclass(frozen=True, slots=True)
class ProfileLink:
    environment_id: str
    one_way_delay_s: int
    scheduled_status: Literal["up", "delayed"]
    default_gap_kind: GapKind
    windows: tuple[ScheduleWindow, ...]


@dataclass(frozen=True, slots=True)
class DemoOverride:
    status: LinkStatus
    one_way_delay_s: float | None
    next_contact_at: datetime | None
    gap_kind: GapKind | None
    gap_planned: bool
    set_at: datetime


@dataclass(frozen=True, slots=True)
class LinkState:
    status: LinkStatus
    one_way_delay_s: float | None
    next_contact_at: datetime | None
    last_contact_at: datetime | None
    source: Literal["schedule", "measured", "demo_switch"]
    environment_id: str
    delay_text: str
    delay_source_ids: tuple[str, ...]
    gap_kind: GapKind | None
    gap_planned: bool


def split_delay_text(earth_delay: str, known_ids: set[str]) -> tuple[str, tuple[str, ...]]:
    ids: list[str] = []

    def remove(match: re.Match[str]) -> str:
        tokens = tuple(re.split(r"[,\s]+", match.group()[1:-1].strip()))
        if not tokens or not all(token in known_ids for token in tokens):
            return match.group()
        for value in tokens:
            if value not in ids:
                ids.append(value)
        return ""

    text = re.sub(r"\([^()]*\)", remove, earth_delay)
    return re.sub(r"\s+([.,;:!?])", r"\1", " ".join(text.split())), tuple(ids)


def active_override(override: DemoOverride | None, at: datetime) -> DemoOverride | None:
    if (
        override is not None
        and override.status == "down"
        and override.gap_planned
        and override.next_contact_at is not None
        and at >= override.next_contact_at
    ):
        return None
    return override


def _contact(windows: tuple[ScheduleWindow, ...], window: ScheduleWindow) -> datetime:
    end = window.ends_at
    for following in sorted(windows, key=lambda w: w.starts_at):
        if following.starts_at == end and following.status == "down":
            end = following.ends_at
    return end


def resolve(
    profile: ProfileLink,
    override: DemoOverride | None,
    at: datetime,
    *,
    last_contact_at: datetime | None,
    delay_text: str,
    delay_source_ids: tuple[str, ...],
) -> LinkState:
    override = active_override(override, at)
    status: LinkStatus = profile.scheduled_status
    delay = float(profile.one_way_delay_s)
    contact, gap, planned = None, None, False
    if override is not None:
        status = override.status
        delay = override.one_way_delay_s if override.one_way_delay_s is not None else delay
        if status == "down":
            contact, gap, planned = (
                override.next_contact_at,
                override.gap_kind or profile.default_gap_kind,
                override.gap_planned,
            )
    else:
        window = next((w for w in profile.windows if w.starts_at <= at < w.ends_at), None)
        if window is not None:
            status = window.status
            if status == "down":
                contact, gap, planned = (
                    _contact(profile.windows, window),
                    window.gap_kind or profile.default_gap_kind,
                    window.planned,
                )
    return LinkState(
        status,
        delay,
        contact,
        last_contact_at,
        "demo_switch" if override is not None else "schedule",
        profile.environment_id,
        delay_text,
        delay_source_ids,
        gap,
        planned,
    )


def next_change(profile: ProfileLink, override: DemoOverride | None, at: datetime) -> datetime | None:
    current = active_override(override, at)
    if current is not None:
        return current.next_contact_at if current.status == "down" and current.gap_planned else None
    return min((b for w in profile.windows for b in (w.starts_at, w.ends_at) if b > at), default=None)


def window_findings(windows: tuple[ScheduleWindow, ...]) -> tuple[str, ...]:
    findings: list[str] = []
    for i, window in enumerate(windows):
        if window.ends_at <= window.starts_at:
            findings.append(f"window {i}: ends_at must follow starts_at")
        if window.status == "down" and window.gap_kind is None:
            findings.append(f"window {i}: down requires gap_kind")
        if window.status != "down" and (window.gap_kind is not None or window.planned):
            findings.append(f"window {i}: available link cannot have gap fields")
        for j, earlier in enumerate(windows[:i]):
            if window.starts_at < earlier.ends_at and earlier.starts_at < window.ends_at:
                findings.append(f"window {i}: overlaps window {j}")
    return tuple(findings)


def validate_demo(
    status: str,
    one_way_delay_s: float | None,
    next_contact_at: datetime | None,
    gap_kind: str | None,
    gap_planned: bool,
) -> None:
    if one_way_delay_s is not None and (not math.isfinite(one_way_delay_s) or one_way_delay_s < 0):
        raise DemoSwitchInvalid("/one_way_delay_s")
    if status not in ("up", "delayed", "down"):
        raise DemoSwitchInvalid("/status")
    if status != "down":
        for field, invalid in (
            ("/next_contact_at", next_contact_at is not None),
            ("/gap_kind", gap_kind is not None),
            ("/gap_planned", gap_planned),
        ):
            if invalid:
                raise DemoSwitchInvalid(field)
