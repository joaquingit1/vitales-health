"""Presentation hold and Act routing during sleep (E24 3.12, V74, Q9, D2).

The warning lifecycle never waits. Only presentation is held: for a person's session, a non-emergency
warning raised inside a hold span (sleep, walks) is held. Emergency is never held, and the shared
habitat screen is never held. Q9: an Act warning goes at once to someone awake; a sleeper is woken only
if nobody awake acknowledges within the configured time, or at once when nobody is awake, the time is
not configured, or no awake responder may receive it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from crew_health.rules.domain.views import HoldState
from crew_health.rules.domain.vocabulary import OVERLAY_SLEEP, Level
from crew_health.rules.domain.warning import Warning


@dataclass(frozen=True, slots=True)
class OverlayFact:
    overlay: str
    start: datetime
    end: datetime | None  # `None`: still active


@dataclass(frozen=True, slots=True)
class HoldSpan:
    start: datetime
    end: datetime | None
    overlays: frozenset[str]

    def contains(self, at: datetime) -> bool:
        return self.start <= at and (self.end is None or at < self.end)


def spans(overlays: Iterable[OverlayFact], suppressed: frozenset[str]) -> tuple[HoldSpan, ...]:
    """Maximal intervals in which at least one suppressing overlay is active; touching ones merge."""
    items = sorted((o for o in overlays if o.overlay in suppressed), key=lambda o: (o.start, o.overlay))
    merged: list[HoldSpan] = []
    for o in items:
        if merged and (merged[-1].end is None or o.start <= merged[-1].end):
            last = merged[-1]
            end = None if last.end is None or o.end is None else max(last.end, o.end)
            merged[-1] = HoldSpan(last.start, end, last.overlays | {o.overlay})
        else:
            merged.append(HoldSpan(o.start, o.end, frozenset({o.overlay})))
    return tuple(merged)


def held_state(
    level: Level,
    raised_at: datetime,
    hold_spans: Sequence[HoldSpan],
    *,
    person_session: bool,
    act_woken: bool = False,
) -> HoldState | None:
    """The hold of one warning for one session. A sleeper whose Act is woken is not held."""
    if not person_session or level is Level.EMERGENCY:
        return None
    for span in hold_spans:
        if not span.contains(raised_at):
            continue
        if level is Level.ACT and act_woken and span.overlays <= {OVERLAY_SLEEP}:
            return None
        return HoldState(span.start, span.end)
    return None


def held_items(candidates: Iterable[Warning], span: HoldSpan, *, person_id: str) -> tuple[Warning, ...]:
    """The warnings held for a person in a span, for the one-interruption summary after it.

    A warning that cleared while held stays in the summary with its clear reason; acknowledging a held
    warning on another device removes it from this person's summary.
    """
    out = [
        w
        for w in candidates
        if w.level is not Level.EMERGENCY
        and span.contains(w.raised_at)
        and all(a.person_id != person_id for a in w.acknowledgements)
    ]
    return tuple(sorted(out, key=lambda w: (w.raised_at, w.warning_id)))


@dataclass(frozen=True, slots=True)
class ActRouting:
    notify_now: tuple[str, ...]
    wake_now: tuple[str, ...]
    wake_after_seconds: int | None  # an elapsed deadline to schedule, else `None`


def route_act(
    *,
    sleepers: Iterable[str],
    awake_responders: Iterable[str],
    acknowledged_by: Iterable[str],
    elapsed_seconds: int,
    wake_after_seconds: int | None,
    responders_may_receive: bool,
) -> ActRouting:
    """Q9. Who is told now, who is woken now and whether to schedule the wake.

    No interval, recipient or consent is guessed: an unconfigured interval, no awake responder or no
    disclosure basis wakes the sleepers at once.
    """
    asleep = tuple(sorted(set(sleepers)))
    responders = tuple(sorted(set(awake_responders)))
    if not asleep:
        return ActRouting(responders if responders_may_receive else (), (), None)
    if not responders or not responders_may_receive or wake_after_seconds is None:
        return ActRouting(responders if responders_may_receive else (), asleep, None)
    if set(acknowledged_by) & set(responders):
        return ActRouting(responders, (), None)
    if elapsed_seconds >= wake_after_seconds:
        return ActRouting(responders, asleep, None)
    return ActRouting(responders, (), wake_after_seconds)
