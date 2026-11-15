"""HelpAssistantSink and HelpTurnGuardPort (E26 4.3) and the runtime status consumer (E26 3.6, 4.4).

The sink applies only `assistant`, `turn` and log lines; the guard decides what may be shown and renders it from
configuration. Nothing here can change the node, the ticks, the level, the helpers, the broadcast or the state.
"""

from __future__ import annotations

from crew_health.help.application.ports import HelpGuardResult, HelpTurnCandidate
from crew_health.help.application.support import (
    DocsCache,
    HelpDeps,
    audit,
    bounds,
    optional_call,
    publish_session,
)
from crew_health.help.domain.docs import PinnedHelpDocs, normalize_text
from crew_health.help.domain.errors import HelpNotFound
from crew_health.help.domain.session import AssistantStatus, HelpSession
from crew_health.help.domain.turn_guard import (
    AssistantCandidate,
    GuardContext,
    GuardResult,
    TextRules,
    ToolCallView,
    check,
)
from crew_health.help.domain.turns import runtime_status, turn_completed, turn_failed
from crew_health.kernel.uow import Actor, UnitOfWork

_REJECTED = GuardResult(False, ("help_flow",), ("/",), (), None)
_ENGINE = Actor("engine", "help")


class AssistantSink:
    def __init__(self, deps: HelpDeps, cache: DocsCache) -> None:
        self._deps, self._cache = deps, cache

    def _text_rules(self, docs: PinnedHelpDocs) -> TextRules | None:
        port = self._deps.text_rules
        if port is None:
            return None

        def load() -> TextRules:
            pattern = port.amount_pattern(docs.release_id)
            terms = frozenset(normalize_text(t) for t in port.medicine_terms(docs.release_id))
            return TextRules(pattern, terms)

        return optional_call(self._deps, "text_rules", load, None)

    def context(self, session: HelpSession, docs: PinnedHelpDocs) -> GuardContext:
        flow = docs.flows.flow(session.node_id) if session.node_kind == "flow" else None
        gate = self._deps.measurement_gate
        eligible: frozenset[int] = frozenset()
        if gate is not None and flow is not None:
            eligible = optional_call(
                self._deps,
                "measurement_gate",
                lambda: frozenset(gate.eligible_measurements(flow.id, docs.release_id, session.origin_mode)),
                eligible,
            )
        return GuardContext(
            flow, self._text_rules(docs), eligible, (), bounds(self._deps).limits.note_max_chars
        )

    def check(self, help_session_id: str, candidate: HelpTurnCandidate) -> HelpGuardResult:
        """HelpTurnGuardPort: the same guard E26 runs at commit, served to E13 (no I/O beyond the read)."""
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            session = deps.sessions.get(snap, help_session_id)
            if session is None:
                raise HelpNotFound()
            docs = self._cache.pinned(session)
            return _result(check(_candidate(candidate), self.context(session, docs)))

    def turn_started(self, help_turn_id: str) -> None:
        """E13 reports a start it already returned from `start_help_turn`; the turn is running from that result."""
        return None

    def _guard_context(self, help_turn_id: str) -> GuardContext | None:
        """Read before the turn's transaction (E26 4.5): optional ports are never called while it is open."""
        deps = self._deps
        with deps.snapshots.snapshot() as snap:
            session = deps.sessions.session_for_turn(snap, help_turn_id)
        if session is None:
            return None
        return self.context(session, self._cache.pinned(session))

    def turn_completed(self, help_turn_id: str, candidate: HelpTurnCandidate) -> HelpGuardResult:
        deps = self._deps
        context = self._guard_context(help_turn_id)
        with deps.uows.begin(
            actor=Actor("assistant", "assistant"), correlation_id=deps.ids.new("hlc")
        ) as uow:
            session = deps.sessions.session_for_turn(uow, help_turn_id)
            turn = session.turn if session is not None else None
            if session is None or turn is None or turn.help_turn_id != help_turn_id or context is None:
                audit(deps, uow, "help.assistant_turn_late", {"help_turn_id": help_turn_id}, {})
                return _result(_REJECTED)
            docs = self._cache.pinned(session)
            # A context read before a node change is stale anyway: the epoch check below refuses the turn.
            result = check(_candidate(candidate), context)
            display = result.display_text if result.accepted else None
            citations = tuple(c.token() for c in result.citations)
            changed, outcome = turn_completed(
                session, help_turn_id, turn.epoch, display, citations, docs, uow.now
            )
            refs = {"help_session_id": session.help_session_id, "help_turn_id": help_turn_id}
            if outcome != "applied":
                audit(deps, uow, f"help.assistant_turn_{outcome}", refs, {})
                return _result(_REJECTED)
            if not result.accepted:
                audit(deps, uow, "help.assistant_turn_rejected", refs, {"rules": ",".join(result.rule_ids)})
            self._save(uow, session, changed, docs)
            return _result(result)

    def turn_failed(self, help_turn_id: str, error_code: str) -> None:
        deps = self._deps
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            session = deps.sessions.session_for_turn(uow, help_turn_id)
            if session is None:
                return
            changed = turn_failed(session, help_turn_id, uow.now)
            self._save(uow, session, changed, self._cache.pinned(session))

    def runtime_status(self, status: AssistantStatus) -> None:
        """`assistant.status_changed`: every open session shows it; `stopped` logs the stopped text once."""
        deps = self._deps
        with deps.uows.begin(actor=_ENGINE, correlation_id=deps.ids.new("hlc")) as uow:
            for session in deps.sessions.open_sessions(uow, bounds(deps).max_open_sessions):
                docs = self._cache.pinned(session)
                self._save(uow, session, runtime_status(session, status, docs, uow.now), docs)

    def _save(self, uow: UnitOfWork, before: HelpSession, after: HelpSession, docs: PinnedHelpDocs) -> None:
        if after == before:
            return
        self._deps.sessions.save(uow, after, before.revision)
        publish_session(self._deps, uow, after, docs)


def _candidate(candidate: HelpTurnCandidate) -> AssistantCandidate:
    calls = tuple(ToolCallView(c.name, dict(c.arguments)) for c in candidate.tool_calls)
    action = dict(candidate.action) if candidate.action is not None else None
    return AssistantCandidate(
        candidate.help_turn_id, candidate.text, action, calls, tuple(candidate.limitations)
    )


def _result(result: GuardResult) -> HelpGuardResult:
    citations = tuple((c.kind, c.ref) for c in result.citations)
    return HelpGuardResult(
        result.accepted, result.rule_ids, result.field_paths, citations, result.display_text
    )
