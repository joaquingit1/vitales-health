"""Typed domain refusals (E05 3). The pure domain names the contract code as text and never imports the generated
contracts (domain-pure); the application maps each refusal to the closed error envelope. Detail codes only: no
private text reaches a log or an envelope."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

type RefusalKind = Literal["INVALID_TRANSITION", "ACTION_NOT_ALLOWED", "INTERNAL_ERROR"]


class DomainRefusal(Exception):
    def __init__(
        self,
        kind: RefusalKind,
        detail_code: str,
        *,
        field: str | None = None,
        current_state: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(detail_code)
        self.kind: RefusalKind = kind
        self.detail_code = detail_code
        self.field = field
        self.current_state = None if current_state is None else dict(current_state)


class InvalidTransition(DomainRefusal):
    def __init__(self) -> None:
        super().__init__("INVALID_TRANSITION", "invalid_transition")


class ActionNotAllowed(DomainRefusal):
    def __init__(self, detail_code: str = "action_not_allowed") -> None:
        super().__init__("ACTION_NOT_ALLOWED", detail_code, field="/action")


class MeasurementInvariant(DomainRefusal):
    """A broken internal invariant: never a client error, always a loud failure."""

    def __init__(self, name: str) -> None:
        super().__init__("INTERNAL_ERROR", name)
