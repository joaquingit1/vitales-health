"""Which stored steps a warning may serve (E24 3.16, DE24-13, S8.2, D20).

Detection never depends on review. The steps are served only when the shared guidance eligibility rule
(E27) says show for the warning's exact content hash, release and purpose; missing source, purpose,
hash, pin or withdrawal, or an absent or failing provider, is the unsupported state and the human path.
Missing clinical review alone is never a block (Q1, Q7, Q18, D20): the exact review label is preserved
and shown. The stored steps are never altered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.rules.domain.views import GuidanceMode
from crew_health.rules.domain.vocabulary import OriginMode
from crew_health.rules.domain.warning import Warning

type Outcome = Literal["show", "show_with_substitute", "not_here", "not_supported"]  # internal enum
type Label = Literal["live", "replay", "simulation_only"]  # internal enum
type Review = Literal["clinically_reviewed", "not_clinically_reviewed"]  # internal enum

# Interim until R-E24-23 (a) adds the `warning_steps` content kind: the data the eligibility request needs.
PURPOSE = "patient_instruction"


@dataclass(frozen=True, slots=True)
class StepsContentRef:
    """The identity of the steps whose eligibility is asked: the definition and its pinned content hash."""

    definition_id: str
    content_sha256: str
    release_id: str
    origin_mode: OriginMode
    purpose: str


@dataclass(frozen=True, slots=True)
class GuidanceVerdict:
    """What the eligibility rule answered. `None` for the whole verdict means the provider is absent or failed."""

    outcome: Outcome
    label: Label
    clinical_review: Review


@dataclass(frozen=True, slots=True)
class ServedSteps:
    mode: GuidanceMode
    steps: tuple[str, ...]
    review_label: Review | None


def content_ref(w: Warning) -> StepsContentRef:
    return StepsContentRef(w.definition_id, w.content_hash, w.config_release_id, w.origin_mode, PURPOSE)


_UNSUPPORTED = ServedSteps("unsupported", (), None)


def serve(w: Warning, verdict: GuidanceVerdict | None) -> ServedSteps:
    """Fails closed: anything but an explicit show for this origin is the unsupported state."""
    if verdict is None or verdict.outcome != "show":
        return _UNSUPPORTED
    live = w.origin_mode is OriginMode.LIVE
    if live and verdict.label != "live":
        return _UNSUPPORTED  # simulation-only content never reaches a LIVE warning
    mode: GuidanceMode = "show" if verdict.label == "live" else "simulation_only"
    return ServedSteps(mode, w.steps, verdict.clinical_review)
