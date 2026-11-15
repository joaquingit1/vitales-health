"""Composition adapters for the plans module's consumer-owned ground link ports (E28 ground_log, SYSTEM 5.12).

Plans declares `GroundLinkReader` and `InTimeWords` as its own Protocol classes; the registry is keyed by class
object, so E29's `GroundLinkQuery` never resolves under them by structure. This module registers adapters under the
plans classes, reading E29's local link state in a fresh snapshot. A port is provided only when it is still absent
and its source is installed; otherwise plans keeps its fail-closed Unknown line.

Plan step availability is not bound here: it rests on guidance eligibility, whose content facts (E10, R-E10-17),
environment facts (E23) and stock facts (E32) have no installed provider, and R-E10-17 forbids a permissive fake.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.groundlink.application.ports import GroundLinkQuery
from crew_health.kernel.uow import ReadSnapshotFactory
from crew_health.plans.application.ground_log import GroundLinkReader, InTimeWords, LinkSnapshot
from crew_health.plans.application.release import UiCopy


@dataclass(frozen=True, slots=True)
class PlansGroundLink:
    """plans GroundLinkReader from E29's GroundLinkQuery. Only a measured link reads as LIVE; a schedule or demo
    switch state is simulated. An unknown environment raises, which plans renders as its Unknown line."""

    query: GroundLinkQuery
    snapshots: ReadSnapshotFactory

    def current(self) -> LinkSnapshot:
        with self.snapshots.snapshot() as view:
            link = self.query.current(view)
            observed_at = view.now
        if link is None:
            raise LookupError("ground link state unknown")
        return LinkSnapshot(
            status=link.status,
            next_contact_at=link.next_contact_at,
            one_way_delay=None if link.one_way_delay_s is None else timedelta(seconds=link.one_way_delay_s),
            simulated=link.source != "measured",
            observed_at=observed_at,
        )


@dataclass(frozen=True, slots=True)
class PlansInTime:
    """plans InTimeWords: CONTRACTS 26 wording (whole hours rounded down from one hour, else whole minutes rounded
    down) from the active release's ui-copy duration_words. A missing template raises, never a made-up word."""

    snapshots: ReadSnapshotFactory

    def render(self, now: datetime, until: datetime) -> str:
        with self.snapshots.snapshot() as view:
            copy = UiCopy.from_release(view.release)
        seconds = max(0, int((until - now).total_seconds()))
        unit, count = ("hours", seconds // 3600) if seconds >= 3600 else ("minutes", seconds // 60)
        template = None if copy is None else copy.text("duration_words", unit)
        if not template:
            raise LookupError("duration words unavailable")
        return UiCopy.render(template, {"n": str(count)})


def _ground_link(resolver: Resolver) -> PlansGroundLink:
    return PlansGroundLink(
        resolver.require(GroundLinkQuery),  # type: ignore[type-abstract]
        resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
    )


def _in_time(resolver: Resolver) -> PlansInTime:
    return PlansInTime(resolver.require(ReadSnapshotFactory))  # type: ignore[type-abstract]


def compose(registry: ModuleRegistry) -> None:
    """Call from assembly right after `configure`: a port another composer bound keeps its provider, and a missing
    source leaves plans fail closed."""
    query, snapshots = cast(type[object], GroundLinkQuery), cast(type[object], ReadSnapshotFactory)
    entries = (
        (cast(type[object], GroundLinkReader), _ground_link, (query, snapshots)),
        (cast(type[object], InTimeWords), _in_time, (snapshots,)),
    )
    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    for port, factory, requires in entries:
        if port not in providers and all(dependency in providers for dependency in requires):
            registry.provide(port, factory, requires=requires)
