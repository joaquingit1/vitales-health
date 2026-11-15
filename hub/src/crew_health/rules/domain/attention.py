"""The attention policy (E24 3.12), pure over one session's visible active warnings.

Only `act` and `emergency` interrupt; a list row never does. Emergency interrupts at once and is never
held. Every unacknowledged active `act` warning that is not held is shown, most recently raised first,
and none is concealed behind a lower level; past the banner bound the rest are counted ("and n more").
Acknowledging removes an item from this person's attention only; the warning stays open.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from crew_health.rules.domain.views import Attention
from crew_health.rules.domain.vocabulary import ACTIVE, Level
from crew_health.rules.domain.warning import Warning


@dataclass(frozen=True, slots=True)
class AttentionPlan:
    emergencies: tuple[str, ...]
    banner: tuple[str, ...]
    more: int
    per_warning: Mapping[str, Attention]


def _recent_first(w: Warning) -> tuple[int, float, str]:
    return (-w.level.rank, -w.raised_at.timestamp(), w.warning_id)


def _acknowledged(w: Warning, person_id: str | None) -> bool:
    return person_id is not None and any(a.person_id == person_id for a in w.acknowledgements)


def plan(
    warnings: Iterable[Warning],
    *,
    person_id: str | None,
    held_ids: Collection[str],
    max_banner_items: int,
) -> AttentionPlan:
    active = sorted((w for w in warnings if w.state in ACTIVE), key=_recent_first)
    emergencies: list[str] = []
    acts: list[str] = []
    for w in active:
        if _acknowledged(w, person_id):
            continue
        if w.level is Level.EMERGENCY:
            emergencies.append(w.warning_id)
        elif w.level is Level.ACT and w.warning_id not in held_ids:
            acts.append(w.warning_id)
    interrupting = emergencies + acts
    order = {wid: i for i, wid in enumerate(interrupting)}
    per: dict[str, Attention] = {}
    for position, w in enumerate(active):
        if w.warning_id in order:
            per[w.warning_id] = Attention(True, order[w.warning_id])
        else:
            per[w.warning_id] = Attention(False, len(interrupting) + position)
    bound = max(0, max_banner_items)
    banner = tuple(acts[:bound])
    return AttentionPlan(tuple(emergencies), banner, len(acts) - len(banner), per)
