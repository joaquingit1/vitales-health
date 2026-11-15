"""Origin rules (E24 10, V72, D16).

`origin_mode` comes from the evidence. A person warning is raised only when its evidence shares the
person's namespace origin, which is the mission mode, and never from REPLAY. Ground silence and supply
cover take SIM when their source is a demonstration, else the mission mode.
"""

from __future__ import annotations

from collections.abc import Iterable

from crew_health.rules.domain.vocabulary import LINK_SOURCE_DEMO_SWITCH, OriginMode


def person_origin_allowed(*, evidence_origins: Iterable[OriginMode], mission_mode: OriginMode) -> bool:
    """True when every evidence entry shares the person's namespace origin and none is REPLAY."""
    origins = set(evidence_origins)
    if not origins:
        return False
    return origins == {mission_mode} and OriginMode.REPLAY not in origins


def ground_origin(source: str, mission_mode: OriginMode) -> OriginMode:
    return OriginMode.SIM if source == LINK_SOURCE_DEMO_SWITCH else mission_mode


def clears_across_origin(*, evidence_origin: OriginMode, warning_origin: OriginMode) -> bool:
    """Evidence of one origin mode never clears a warning of another (REPLAY never clears LIVE or SIM)."""
    return evidence_origin is warning_origin
