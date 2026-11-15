"""Offline path replay (CONTRACTS 16 "Opening" step 2, E26 3.2, I-H14).

Answers taken on a cached first screen are applied in order at their client times. Replay stops at the first
entry that names another node, an answer that does not exist, or (when the client ran another responder-flows
version) an answer whose label or destination differs between the two versions. Nothing after a stop applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.help.domain.docs import FlowDocument, PinnedHelpDocs, normalize_text
from crew_health.help.domain.errors import HelpError
from crew_health.help.domain.session import HelpCommand, HelpLimits, HelpSession, Who, apply

StopReason = Literal["node_mismatch", "release_mismatch", "release_unreadable", "rejected", "limit"]


@dataclass(frozen=True, slots=True)
class OfflineAnswer:
    node_id: str
    answer_index: int
    at: datetime


@dataclass(frozen=True, slots=True)
class ClientFlows:
    """The responder-flows document the client answered from: same version, another readable one, or unreadable."""

    same_version: bool
    document: FlowDocument | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    session: HelpSession
    applied_count: int
    stop_reason: StopReason | None
    stop_index: int | None
    stop_node_id: str | None


def _same_meaning(client: FlowDocument, pinned: FlowDocument, node_id: str, index: int) -> bool:
    mine = pinned.triage_node(node_id)
    theirs = client.triage_node(node_id)
    if mine is None or theirs is None or index >= len(mine.answers) or index >= len(theirs.answers):
        return False
    a, b = mine.answers[index], theirs.answers[index]
    return normalize_text(a.label) == normalize_text(b.label) and a.goes_to == b.goes_to


def replay(
    session: HelpSession,
    entries: Sequence[OfflineAnswer],
    who: Who,
    docs: PinnedHelpDocs,
    limits: HelpLimits,
    *,
    client: ClientFlows,
    now: datetime,
) -> ReplayResult:
    if len(entries) > limits.offline_path_max:
        return ReplayResult(session, 0, "limit", 0, None)
    current = session
    for index, entry in enumerate(entries):
        reason: StopReason | None = None
        if not client.same_version and client.document is None:
            reason = "release_unreadable"
        elif current.node_kind != "triage" or entry.node_id != current.node_id:
            reason = "node_mismatch"
        elif not client.same_version and client.document is not None:
            if not _same_meaning(client.document, docs.flows, entry.node_id, entry.answer_index):
                reason = "release_mismatch"
        if reason is None:
            command = HelpCommand("answer", entry.at, node_id=entry.node_id, answer_index=entry.answer_index)
            try:
                current = apply(
                    current, command, who, docs, limits, now=now, if_match=current.revision, replayed=True
                )
            except HelpError:
                reason = "rejected"
        if reason is not None:
            return ReplayResult(current, index, reason, index, entry.node_id)
    return ReplayResult(current, len(entries), None, None, None)
