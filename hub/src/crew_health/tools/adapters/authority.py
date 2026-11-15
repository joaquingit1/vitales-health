"""E02-backed scope authority for the tool gateway (E12 4.1, S8.3, C17).

Model context and tool calls are limited to the signed-in crew member's own records. The checks go through E02's
published seams: the session must still be live (IdentityPorts.sessions), the subject must be a current roster
member (IdentityPorts.roster), and any stored observation a call names must have that member as its patient
(consent's AuthoredRecords). A crewmate's identifier or display name in an argument or a result is refused. C17 makes
this stricter than consent: a grant, a medical officer role or a helper assignment never opens another member's
chart to inference, so no ConsentQueries grant can widen what the gateway admits.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping

from crew_health.consent.application.ports import AuthoredRecords
from crew_health.identity.application.ports import IdentityPorts, RosterEntry
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.scope import ToolScope, require_own_subject

_WORD = re.compile(r"[^\w]+", re.UNICODE)
_QUERY_TOOLS = frozenset({"search_evidence"})


def _normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " " + " ".join(part for part in _WORD.split(folded) if part) + " "


class SessionAuthority:
    """ScopeAuthority over E02's identity and record-authorship seams. Absent seams mean no access."""

    def __init__(
        self,
        identity: Callable[[], IdentityPorts | None],
        records: Callable[[], AuthoredRecords | None],
        *,
        max_members: int,
        max_strings: int,
    ) -> None:
        if not 1 <= max_members <= 1024 or not 1 <= max_strings <= 100_000:
            raise ToolsInvalid("CONFIG_INVALID")
        self._identity, self._records = identity, records
        self._max_members, self._max_strings = max_members, max_strings

    def require_current(self, uow: UnitOfWork, scope: ToolScope) -> bool:
        identity = self._identity()
        if identity is None:
            return False
        try:
            require_own_subject(scope.session, scope.subject_person_id)
        except ToolsInvalid:
            return False
        subject = scope.subject_person_id
        view: ReadSnapshot = uow
        return (
            subject is not None
            and identity.sessions.alive(view, scope.session)
            and identity.roster.member(uow, subject) is not None
        )

    def authorize_arguments(
        self, uow: UnitOfWork, scope: ToolScope, name: str, arguments: Mapping[str, object]
    ) -> bool:
        identity = self._identity()
        if identity is None or not self.require_current(uow, scope):
            return False
        members = self._members(uow, identity)
        if members is None:
            return False
        strings = self._strings(arguments)
        if strings is None:
            return False
        subject = scope.subject_person_id
        for text in strings:
            if self._names_crewmate(text, members, subject) or not self._own_records(uow, scope, text):
                return False
            if name in _QUERY_TOOLS and self._names_anyone(text, members):
                return False
        return True

    def authorize_result(
        self, uow: UnitOfWork, scope: ToolScope, name: str, data: Mapping[str, object]
    ) -> bool:
        identity = self._identity()
        if identity is None or not self.require_current(uow, scope):
            return False
        members = self._members(uow, identity)
        strings = self._strings(data)
        if members is None or strings is None:
            return False
        return not any(self._names_crewmate(text, members, scope.subject_person_id) for text in strings)

    def _members(self, uow: UnitOfWork, identity: IdentityPorts) -> tuple[RosterEntry, ...] | None:
        members = identity.roster.members(uow, after_index=None, limit=self._max_members + 1)
        return None if len(members) > self._max_members else members

    def _strings(self, value: object) -> tuple[str, ...] | None:
        found: list[str] = []
        for text in _walk(value, self._max_strings):
            if text is None:
                return None
            found.append(text)
        return tuple(found)

    @staticmethod
    def _names_crewmate(text: str, members: tuple[RosterEntry, ...], subject: str | None) -> bool:
        normalized = _normalized(text)
        for member in members:
            if member.person_id == subject:
                continue
            if member.person_id in text or _normalized(member.display_name) in normalized:
                return True
        return False

    @staticmethod
    def _names_anyone(text: str, members: tuple[RosterEntry, ...]) -> bool:
        normalized = _normalized(text)
        return any(
            member.person_id in text or _normalized(member.display_name) in normalized for member in members
        )

    def _own_records(self, uow: UnitOfWork, scope: ToolScope, text: str) -> bool:
        """A string that names a stored observation must name one of the subject's own. Without the authorship
        seam an observation-shaped reference cannot be checked, so it is refused."""
        try:
            require_id(text)
        except ProblemError:
            return True
        records = self._records()
        if records is None:
            return not text.startswith("obs_")
        record = records.observation(uow, text)
        return record is None or record.patient_id == scope.subject_person_id


def _walk(value: object, maximum: int) -> Iterator[str | None]:
    """Every string key and value in a parsed JSON value, iteratively; None once the bound is exceeded."""
    pending: list[object] = [value]
    seen = 0
    while pending:
        seen += 1
        if seen > maximum:
            yield None
            return
        node = pending.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, Mapping):
            for key, item in node.items():
                pending.append(key)
                pending.append(item)
        elif isinstance(node, list | tuple):
            pending.extend(node)
