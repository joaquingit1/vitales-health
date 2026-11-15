"""E26's audit declarations as platform policies (SYSTEM 5.8): every audit event the help module appends, the
reference each one names and the safe metadata values it may carry. Without them the platform's audit trail refuses
every help audit append, so opening a help session failed (AGENTS 1 rule 3: Someone needs help must work).

Metadata is never free text: entry kinds, check names, stop reasons, rule IDs and error codes are their closed
enums; node IDs are configuration IDs (common.schema.json config_id); counts are bounded integers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import get_args

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.help.application.support import AUDIT_REFERENCE_TYPES
from crew_health.help.domain.replay import StopReason
from crew_health.help.domain.turn_guard import RuleId
from crew_health.kernel.audit import AuditPolicy

CONFIG_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# The registered ceiling of help.offline_path_max and help.page_max (help/application/settings.py): no count an
# audit entry carries can exceed the largest bound a setting may take.
COUNT_CEILING = 1000
ENTRY_KINDS = ("button", "red_flag", "warning", "urgent_event_alias")
UNCHECKED = ("roster", "warning")


class ConfigIds(frozenset[str | int | bool]):
    """Every configuration ID: a closed shape, never free text."""

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and CONFIG_ID.fullmatch(value) is not None


class Counts(frozenset[str | int | bool]):
    def __contains__(self, value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= COUNT_CEILING


def _rules() -> frozenset[str | int | bool]:
    """The comma-joined rule IDs a rejected turn names, in the guard's order (turn_guard.py)."""
    ids: tuple[str, ...] = get_args(RuleId)
    values: set[str | int | bool] = set()
    for mask in range(1, 2 ** len(ids)):
        values.add(",".join(rule for index, rule in enumerate(ids) if mask >> index & 1))
    return frozenset(values)


def _policy(
    event_type: str, refs: Iterable[str], metadata: dict[str, frozenset[str | int | bool]] | None = None
) -> AuditPolicy:
    fields = frozenset(refs)
    return AuditPolicy(
        event_type,
        metadata or {},
        fields,
        {name: frozenset({AUDIT_REFERENCE_TYPES[name]}) for name in fields},
    )


def audit_policies() -> tuple[AuditPolicy, ...]:
    session, turn = ("help_session_id",), ("help_session_id", "help_turn_id")
    message = ("ground_message_id",)
    return (
        _policy("help.opened", session, {"entry": frozenset(ENTRY_KINDS)}),
        _policy("help.ref_unchecked", session, {"check": frozenset(UNCHECKED)}),
        _policy(
            "help.replay_stopped",
            session,
            {
                "reason": frozenset(get_args(StopReason)),
                "applied": Counts(),
                "index": Counts(),
                "node_id": ConfigIds(),
                "at_node_id": ConfigIds(),
            },
        ),
        _policy("help.subject_viewed", session, {"readings": Counts()}),
        _policy("help.reading_future_excluded", session, {"count": Counts()}),
        _policy("help.command_rejected", ("target",), {"code": frozenset(code.value for code in ErrorCode)}),
        _policy("help.assistant_turn_late", ("help_turn_id",)),
        _policy("help.assistant_turn_stale", turn),
        _policy("help.assistant_turn_rejected", turn, {"rules": _rules()}),
        _policy("ground_message.queued", message),
        _policy("ground_message.event_duplicate", message),
    )
