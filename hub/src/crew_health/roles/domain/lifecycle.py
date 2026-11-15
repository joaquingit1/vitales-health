"""RoleAssignment lifecycle (E31 design 3.4): proposed, accepted, changed; kept, replaced and retired rows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.roles.domain.engine import ContextKind, Decided, Proposal, need_key
from crew_health.roles.domain.errors import HumanOnly, NotAssignee

type AssignmentState = Literal["proposed", "accepted", "changed"]
type ActorKind = Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"]

# Only a person's or the station's own session may accept or reassign (AGENTS rule 4, design section 10).
HUMAN_ACTORS: frozenset[str] = frozenset({"person", "station"})


@dataclass(frozen=True, slots=True)
class ContextReason:
    reason_code: str
    text: str


@dataclass(frozen=True, slots=True)
class ChangedBy:
    kind: str
    id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class Assignment:
    role_assignment_id: str
    revision: int
    context_kind: ContextKind
    context_id: str
    role_id: str
    label: str
    person_id: str
    person_display_name: str
    skill_id: str | None
    task_text: str | None
    reason_code: str
    reason_text: str
    context_reasons: tuple[ContextReason, ...]
    state: AssignmentState
    proposed_at: datetime
    accepted_at: datetime | None
    changed_by: ChangedBy | None
    changed_at: datetime | None
    replaced_by_id: str | None
    retired_at: datetime | None
    retired_reason: str | None
    acting_since: datetime | None
    acting_ended_at: datetime | None
    config_release_id: str

    @property
    def current(self) -> bool:
        return self.replaced_by_id is None and self.retired_at is None

    @property
    def human_decided(self) -> bool:
        return self.state == "accepted" or self.changed_by is not None

    def decided(self) -> Decided:
        return Decided(self.role_assignment_id, self.role_id, self.skill_id, self.task_text, self.person_id)


@dataclass(frozen=True, slots=True)
class Diff:
    """What a proposal changes: rows to insert, rows replaced by them, rows retired with a reason."""

    insert: tuple[int, ...]  # indexes into Proposal.proposed
    replace: tuple[tuple[str, int], ...]  # (old role_assignment_id, index of the new row)
    retire: tuple[tuple[str, str], ...]  # (role_assignment_id, reason)
    unchanged: tuple[str, ...]


def require_human(actor_kind: str) -> None:
    """The assistant, the engine and machine actors never accept or set a role (AGENTS rule 4)."""
    if actor_kind not in HUMAN_ACTORS:
        raise HumanOnly(actor_kind)


def _same(old: Assignment, person_id: str, reason_text: str, context: tuple[ContextReason, ...]) -> bool:
    return old.person_id == person_id and old.reason_text == reason_text and old.context_reasons == context


def diff(
    current: Sequence[Assignment],
    proposal: Proposal,
    rendered: Sequence[tuple[str, tuple[ContextReason, ...]]],
) -> Diff:
    """Compare current rows with a proposal; `rendered[i]` is the reason text and context of proposed[i].

    Human-decided rows the engine kept stay untouched; the ones it retired leave with their reason.
    A proposed row is untouched when the new proposal for its need is identical, replaced when it differs,
    and retired when its need is now unfilled or gone.
    """
    kept_ids = {d.role_assignment_id for d in proposal.kept}
    retire: list[tuple[str, str]] = [(d.role_assignment_id, reason) for d, reason in proposal.retired]
    retired_ids = {r for r, _ in retire}
    by_key: dict[tuple[str, str, str], int] = {}
    for index, item in enumerate(proposal.proposed):
        by_key.setdefault(need_key(item.need), index)
    unfilled_keys = {need_key(n) for n in proposal.unfilled}
    replace_pairs: list[tuple[str, int]] = []
    unchanged: list[str] = []
    claimed: set[int] = set()
    same: set[int] = set()
    for old in sorted(current, key=lambda a: a.role_assignment_id):
        if not old.current or old.human_decided or old.role_assignment_id in kept_ids | retired_ids:
            continue
        key = need_key(old.decided())
        found = by_key.get(key)
        if found is None or found in claimed:
            retire.append(
                (old.role_assignment_id, "no_candidate" if key in unfilled_keys else "need_changed")
            )
            continue
        index = found
        claimed.add(index)
        text, context = rendered[index]
        if _same(old, proposal.proposed[index].person_id, text, context):
            unchanged.append(old.role_assignment_id)
            same.add(index)
        else:
            replace_pairs.append((old.role_assignment_id, index))
    return Diff(
        insert=tuple(i for i in range(len(proposal.proposed)) if i not in same),
        replace=tuple(replace_pairs),
        retire=tuple(retire),
        unchanged=tuple(unchanged),
    )


def accept(assignment: Assignment, *, person_id: str | None, actor_kind: str, now: datetime) -> Assignment:
    """`accept` sets accepted; only the assigned person's own session (CONTRACTS 24)."""
    require_human(actor_kind)
    if person_id is None or person_id != assignment.person_id:
        raise NotAssignee(assignment.role_assignment_id)
    return replace(assignment, state="accepted", accepted_at=now, revision=assignment.revision + 1)


def reassign(
    assignment: Assignment,
    *,
    person_id: str,
    person_display_name: str,
    changed_by: ChangedBy,
    reason_text: str,
    actor_kind: str,
    now: datetime,
) -> Assignment:
    """In place: the new person, `changed`, `changed_by_helper` (CONTRACTS 24). Exclusions are checked by the caller."""
    require_human(actor_kind)
    return replace(
        assignment,
        person_id=person_id,
        person_display_name=person_display_name,
        state="changed",
        reason_code="changed_by_helper",
        reason_text=reason_text,
        context_reasons=(),
        changed_by=changed_by,
        changed_at=now,
        accepted_at=None,
        acting_since=None,
        acting_ended_at=assignment.acting_ended_at if assignment.acting_since is None else now,
        revision=assignment.revision + 1,
    )


def allowed_actions(
    assignment: Assignment, *, session_person_id: str | None, context_open: bool
) -> list[str]:
    if not context_open or not assignment.current:
        return []
    actions: list[str] = []
    if assignment.state in ("proposed", "changed") and session_person_id == assignment.person_id:
        actions.append("accept")
    actions.append("reassign")
    return actions
