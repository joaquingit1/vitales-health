from __future__ import annotations

from collections.abc import Sequence

from crew_health.config.application.ports import TextMatches, TextRulesPort
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.settings import HubSettings
from crew_health.tools.application.ports import PolicyVerdict, TextField, TurnData
from crew_health.tools.domain.arguments import JsonBounds, text_fields
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.settings import argument_bounds


def _require_clean(matches: TextMatches, release: ReleaseHandle) -> None:
    if matches.state != "checked" or matches.release_id != release.release_id or matches.spans:
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")


def require_text_checks(
    raw: bytes, *, bounds: JsonBounds, rules: TextRulesPort | None, release: ReleaseHandle
) -> None:
    """Amount and medicine checks only. E13 must also enforce every semantic and owner guard."""
    if rules is None:
        raise ToolsInvalid("CAPABILITY_UNAVAILABLE")
    for text in text_fields(raw, bounds):
        _require_clean(rules.find_amounts(text, release=release), release)
        _require_clean(rules.find_medicines(text, release=release), release)


class OutputPolicy:
    """Only owner-authored exact text is publishable without the pending semantic-verifier binding."""

    def __init__(self, rules: TextRulesPort | None, settings: HubSettings) -> None:
        self._rules = rules
        self._bounds = argument_bounds(settings)
        self._maximum = settings.integer("tools.context.max_items")

    def check(self, fields: Sequence[TextField], scope: ToolScope, turn: TurnData) -> PolicyVerdict:
        if (
            turn.release.release_id != scope.config_release_id
            or turn.source_revision != scope.source_revision
            or len(fields) > self._maximum
            or len(turn.authored_fields) > self._maximum
        ):
            return PolicyVerdict(False, "MODEL_OUTPUT_REJECTED")
        raw = canonical_json(
            [{"field": field.field, "text": field.text, "source_ref": field.source_ref} for field in fields]
        )
        try:
            require_text_checks(raw, bounds=self._bounds, rules=self._rules, release=turn.release)
        except ToolsInvalid as error:
            if error.code == "CAPABILITY_UNAVAILABLE":
                return PolicyVerdict(False, "CAPABILITY_UNAVAILABLE")
            if error.code == "CONFIG_INVALID":
                raise
            return PolicyVerdict(False, "MODEL_OUTPUT_REJECTED")
        authored = frozenset(
            (field.field, field.text, field.source_ref)
            for field in turn.authored_fields
            if field.source_ref is not None
        )
        if any((field.field, field.text, field.source_ref) not in authored for field in fields):
            return PolicyVerdict(False, "MODEL_OUTPUT_REJECTED")
        return PolicyVerdict(True, None)
