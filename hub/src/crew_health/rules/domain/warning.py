"""The warning entity and its transitions (E24 3.3, 3.4).

Hold, attention, served steps and `allowed_actions` are session projections computed on read and never
stored. A transition returns a new value with `revision + 1`. No transition changes `level`, `raised_by`,
`definition_id`, `scope_key`, `trigger`, `evidence`, `steps`, `raised_at`, `origin_mode` or
`origin_node_id`, and final states have no outgoing transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

from crew_health.kernel.clock import require_utc
from crew_health.rules.domain.definitions import WarningDefinition
from crew_health.rules.domain.errors import (
    ActionNotAllowed,
    InvalidTransition,
    InvariantViolation,
    LevelNotAllowed,
    OriginMismatch,
)
from crew_health.rules.domain.vocabulary import (
    ACTIVE,
    ASSISTANT_LEVELS,
    BODY_REGIONS,
    Basis,
    ClearReason,
    ConditionKind,
    EnvironmentId,
    EvidenceKind,
    Level,
    OriginMode,
    RaisedBy,
    Scope,
    WarningState,
)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: EvidenceKind
    ref_id: str
    summary: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Citation:
    text: str
    source_ids: tuple[str, ...]
    basis: Basis | None


@dataclass(frozen=True, slots=True)
class HeadlineFields:
    fragment: str
    rank: int
    solo: bool


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    person_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Resolution:
    by_id: str
    by_kind: str
    at: datetime
    reason: str
    role: str | None  # for example `acting_medical_officer`; recorded, never on the wire (D21)


@dataclass(frozen=True, slots=True)
class Dismissal:
    by_id: str
    at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class Note:
    by_id: str
    at: datetime
    text: str


@dataclass(frozen=True, slots=True)
class TriggerRef:
    kind: ConditionKind
    ref: str


@dataclass(frozen=True, slots=True)
class LimitPin:
    """What a sensor-limit warning clears by, frozen at raise (D1)."""

    limit_id: str
    limit_fingerprint: str
    dwell_seconds: int
    stale_after_seconds: int
    basis: Basis | None
    clear_basis: Basis | None


@dataclass(frozen=True, slots=True)
class DefinitionPin:
    """The definition, and for sensor limits the limit, an active warning was raised under (D1)."""

    definition: WarningDefinition
    limit: LimitPin | None


@dataclass(frozen=True, slots=True)
class PersonActor:
    person_id: str


@dataclass(frozen=True, slots=True)
class AssistantActor:
    id: str


@dataclass(frozen=True, slots=True)
class SessionActor:
    """A non-person session: station, ground, operator, adapter or editor."""

    kind: str
    id: str


_ENGINE_TOKEN: Final[object] = object()


@dataclass(frozen=True, slots=True)
class EngineActor:
    """The deterministic engine of the warning's origin node. Built only by `engine_actor`."""

    node_id: str
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ENGINE_TOKEN:
            raise ActionNotAllowed("an engine actor is not constructible from a session")


def engine_actor(node_id: str, *, origin_node_id: str) -> EngineActor:
    """Only the origin node's engine clears, resolves or supersedes a warning (D4.4, INV-18)."""
    if not node_id or node_id != origin_node_id:
        raise OriginMismatch("only the origin node changes a warning")
    return EngineActor(node_id, _ENGINE_TOKEN)


type Actor = PersonActor | AssistantActor | SessionActor | EngineActor

# Clear reasons the engine may produce. `config_changed` and `environment_changed` never clear a live
# warning (D1); they stay in the vocabulary for stored and replicated records only.
ENGINE_CLEAR_REASONS: Final[frozenset[ClearReason]] = frozenset(
    {
        ClearReason.CONDITION_FALSE,
        ClearReason.CLEAR_EVENT,
        ClearReason.EXPIRED,
        ClearReason.SUPERSEDED,
    }
)


@dataclass(frozen=True, slots=True)
class Warning:
    warning_id: str
    revision: int
    definition_id: str
    config_release_id: str
    level: Level
    raised_by: RaisedBy
    hazard_id: str | None
    limit_id: str | None
    sensor_type_id: str | None
    channel_id: str | None
    scope: Scope
    person_id: str | None
    environment_id: EnvironmentId
    location: str | None
    origin_mode: OriginMode
    title: str
    what: str
    why: str
    steps: tuple[str, ...]
    body_regions: tuple[str, ...]
    headline: HeadlineFields
    evidence: tuple[EvidenceRef, ...]
    evidence_time: datetime
    evidence_time_label: str
    citation: Citation
    insight_id: str | None
    state: WarningState
    raised_at: datetime
    acknowledgements: tuple[Acknowledgement, ...]
    cleared_at: datetime | None
    clear_reason: ClearReason | None
    resolved: Resolution | None
    dismissed: Dismissal | None
    notes: tuple[Note, ...]
    previous_warning_id: str | None
    routine_override_ids: tuple[str, ...]
    scope_key: str
    trigger: TriggerRef
    rule_fingerprint: str
    content_hash: str
    origin_node_id: str
    pin: DefinitionPin
    source_revision: int | None = None
    source_partition: str | None = None

    def __post_init__(self) -> None:
        for instant in (self.evidence_time, self.raised_at):
            require_utc(instant)
        _check_identity(self)
        _check_state(self)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def key(self) -> tuple[str, str]:
        return (self.definition_id, self.scope_key)


def _check_identity(w: Warning) -> None:
    if w.revision < 1:
        raise InvariantViolation("revision starts at 1")
    if w.raised_by is RaisedBy.ASSISTANT and w.level not in ASSISTANT_LEVELS:
        raise LevelNotAllowed("the assistant raises info or watch only")
    if not w.steps or any(not s for s in w.steps):
        raise InvariantViolation("a warning always has stored steps")
    if not set(w.body_regions) <= BODY_REGIONS or len(set(w.body_regions)) != len(w.body_regions):
        raise InvariantViolation("body regions come from the fixed set")
    if not w.citation.text:
        raise InvariantViolation("a warning always has a citation")
    if not (w.title and w.what and w.why and w.evidence_time_label):
        raise InvariantViolation("a warning always has its words")
    if (w.scope is Scope.PERSON) != (w.person_id is not None):
        raise InvariantViolation("only person scope names a person")
    if w.scope is Scope.PERSON and w.origin_mode is OriginMode.REPLAY:
        raise OriginMismatch("a person warning is never REPLAY")
    if (w.raised_by is RaisedBy.ASSISTANT) != (w.insight_id is not None):
        raise InvariantViolation("an insight is named by assistant warnings only")


def _check_state(w: Warning) -> None:
    if w.state is WarningState.CLEARED and (w.cleared_at is None or w.clear_reason is None):
        raise InvariantViolation("cleared needs its time and reason")
    if w.state is not WarningState.CLEARED and (w.cleared_at is not None or w.clear_reason is not None):
        raise InvariantViolation("only cleared carries a clear time and reason")
    if (w.state is WarningState.RESOLVED) != (w.resolved is not None):
        raise InvariantViolation("resolved state and record go together")
    if (w.state is WarningState.DISMISSED) != (w.dismissed is not None):
        raise InvariantViolation("dismissed state and record go together")
    if w.state is WarningState.RESOLVED and w.raised_by is RaisedBy.ASSISTANT:
        raise ActionNotAllowed("an assistant warning is never resolved")
    if w.state is WarningState.DISMISSED and w.raised_by is not RaisedBy.ASSISTANT:
        raise ActionNotAllowed("a rule or sensor warning is never dismissed")
    if w.state is WarningState.ACKNOWLEDGED and not w.acknowledgements:
        raise InvariantViolation("acknowledged needs an acknowledgement")


@dataclass(frozen=True, slots=True)
class NewWarning:
    """Everything `raise_new` stores, all of it already decided by the engine and the renderer."""

    warning_id: str
    config_release_id: str
    definition: WarningDefinition
    pin: DefinitionPin
    limit_id: str | None
    channel_id: str | None
    person_id: str | None
    environment_id: EnvironmentId
    location: str | None
    origin_mode: OriginMode
    title: str
    what: str
    why: str
    steps: tuple[str, ...]
    headline_fragment: str
    evidence: tuple[EvidenceRef, ...]
    evidence_time: datetime
    evidence_time_label: str
    citation: Citation
    insight_id: str | None
    raised_at: datetime
    previous_warning_id: str | None
    body_regions: tuple[str, ...]
    scope_key: str
    trigger: TriggerRef
    rule_fingerprint: str
    content_hash: str
    origin_node_id: str
    source_revision: int | None = None
    source_partition: str | None = None


def raise_new(n: NewWarning) -> Warning:
    d = n.definition
    if d.raised_by is RaisedBy.ASSISTANT and d.level not in ASSISTANT_LEVELS:
        raise LevelNotAllowed("the assistant raises info or watch only")
    return Warning(
        warning_id=n.warning_id,
        revision=1,
        definition_id=d.id,
        config_release_id=n.config_release_id,
        level=d.level,
        raised_by=d.raised_by,
        hazard_id=d.hazard_id,
        limit_id=n.limit_id,
        sensor_type_id=d.sensor_type_id,
        channel_id=n.channel_id,
        scope=d.scope,
        person_id=n.person_id,
        environment_id=n.environment_id,
        location=n.location,
        origin_mode=n.origin_mode,
        title=n.title,
        what=n.what,
        why=n.why,
        steps=n.steps,
        body_regions=n.body_regions,
        headline=HeadlineFields(n.headline_fragment, d.content.headline.rank, d.content.headline.solo),
        evidence=n.evidence,
        evidence_time=n.evidence_time,
        evidence_time_label=n.evidence_time_label,
        citation=n.citation,
        insight_id=n.insight_id,
        state=WarningState.RAISED,
        raised_at=n.raised_at,
        acknowledgements=(),
        cleared_at=None,
        clear_reason=None,
        resolved=None,
        dismissed=None,
        notes=(),
        previous_warning_id=n.previous_warning_id,
        routine_override_ids=(),
        scope_key=n.scope_key,
        trigger=n.trigger,
        rule_fingerprint=n.rule_fingerprint,
        content_hash=n.content_hash,
        origin_node_id=n.origin_node_id,
        pin=n.pin,
        source_revision=n.source_revision,
        source_partition=n.source_partition,
    )


def _require_active(w: Warning) -> None:
    if w.state not in ACTIVE:
        raise InvalidTransition("a final warning has no outgoing transition")


def _require_person(actor: Actor) -> PersonActor:
    if isinstance(actor, AssistantActor):
        raise ActionNotAllowed("the assistant takes no action on a warning")
    if not isinstance(actor, PersonActor):
        raise ActionNotAllowed("only a person takes this action")
    return actor


def acknowledge(w: Warning, actor: Actor, at: datetime) -> Warning:
    person = _require_person(actor)
    require_utc(at)
    _require_active(w)
    if any(a.person_id == person.person_id for a in w.acknowledgements):
        raise InvalidTransition("already acknowledged by this person")
    ack = Acknowledgement(person.person_id, at)
    return replace(
        w,
        revision=w.revision + 1,
        acknowledgements=(*w.acknowledgements, ack),
        state=WarningState.ACKNOWLEDGED,
    )


def add_note(w: Warning, actor: Actor, at: datetime, text: str, *, max_notes: int) -> Warning:
    person = _require_person(actor)
    require_utc(at)
    _require_active(w)
    if not text:
        raise InvalidTransition("a note is not empty")
    if len(w.notes) >= max_notes:
        raise InvalidTransition("the note bound is reached")
    return replace(w, revision=w.revision + 1, notes=(*w.notes, Note(person.person_id, at, text)))


def clear(w: Warning, actor: Actor, reason: ClearReason, at: datetime) -> Warning:
    """Only the origin node's engine clears a warning, and only for a reason the engine may give."""
    if not isinstance(actor, EngineActor) or actor.node_id != w.origin_node_id:
        raise ActionNotAllowed("only the origin node's engine clears a warning")
    require_utc(at)
    _require_active(w)
    if reason not in ENGINE_CLEAR_REASONS:
        raise ActionNotAllowed("the engine never clears for a configuration or environment change")
    if reason is ClearReason.EXPIRED and w.raised_by is not RaisedBy.ASSISTANT:
        raise InvalidTransition("expiry never ends a rule or sensor warning")
    return replace(w, revision=w.revision + 1, state=WarningState.CLEARED, cleared_at=at, clear_reason=reason)


def resolve(w: Warning, actor: Actor, at: datetime, reason: str, *, role: str | None) -> Warning:
    """Records a resolution. Who may resolve is decided in `actions.py`, never here."""
    person = _require_person(actor)
    require_utc(at)
    _require_active(w)
    if w.raised_by is RaisedBy.ASSISTANT:
        raise ActionNotAllowed("an assistant warning is dismissed, not resolved")
    if not reason.strip():
        raise InvalidTransition("a resolution needs a reason")
    done = Resolution(person.person_id, "person", at, reason, role)
    return replace(w, revision=w.revision + 1, state=WarningState.RESOLVED, resolved=done)


def dismiss(w: Warning, actor: Actor, at: datetime, reason: str | None) -> Warning:
    person = _require_person(actor)
    require_utc(at)
    if w.raised_by is not RaisedBy.ASSISTANT:
        raise ActionNotAllowed("a rule or sensor warning can never be dismissed")
    if w.person_id != person.person_id:
        raise ActionNotAllowed("only the person it concerns dismisses an assistant warning")
    _require_active(w)
    done = Dismissal(person.person_id, at, reason or None)
    return replace(w, revision=w.revision + 1, state=WarningState.DISMISSED, dismissed=done)


def attach_overrides(w: Warning, override_ids: tuple[str, ...]) -> Warning:
    _require_active(w)
    merged = tuple(sorted({*w.routine_override_ids, *override_ids}))
    if merged == w.routine_override_ids:
        return w
    return replace(w, revision=w.revision + 1, routine_override_ids=merged)
