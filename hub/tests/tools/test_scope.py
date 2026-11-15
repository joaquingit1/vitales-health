from __future__ import annotations

from typing import Any, Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.output_policy import require_warning_authority
from crew_health.tools.domain.scope import require_own_subject


@pytest.mark.trace("T-E12-003", "T-E12-008", "I1")
@pytest.mark.parametrize("role", [Role.CREW, Role.CREW_MEDICAL_OFFICER])
def test_role_does_not_allow_inference_on_another_person(role: Role) -> None:
    session = SessionContext("session", "crew", Actor("person", "owner"), "owner", frozenset({role}))
    require_own_subject(session, "owner")
    for subject in (None, "foreign"):
        with pytest.raises(ToolsInvalid):
            require_own_subject(session, subject)


@pytest.mark.trace("T-E12-003", "I1")
@pytest.mark.parametrize("kind", ["station", "ground", "operator", "editor", "adapter"])
def test_noncrew_session_cannot_infer_chart(
    kind: Literal["station", "ground", "operator", "editor", "adapter"],
) -> None:
    session = SessionContext("session", kind, Actor("person", "owner"), "owner", frozenset())
    with pytest.raises(ToolsInvalid):
        require_own_subject(session, "owner")


@pytest.mark.trace("T-E12-051", "R28", "I1")
@given(
    st.sampled_from(["info", "watch", "act", "emergency"]),
    st.sampled_from(["rule", "sensor_limit", "assistant"]),
    st.sampled_from([None, "clear", "acknowledge", "resolve", "lower"]),
)
def test_warning_authority_never_widens(level: str, raised_by: str, action: str | None) -> None:
    allowed = level in {"info", "watch"} and raised_by == "assistant" and action is None
    if allowed:
        require_warning_authority(level=level, raised_by=raised_by, action=action)
    else:
        with pytest.raises(ToolsInvalid):
            require_warning_authority(level=level, raised_by=raised_by, action=action)


@pytest.mark.trace("T-E12-004", "I2")
def test_module_error_uses_generated_sanitized_error_envelope() -> None:
    from crew_health.app.errors import to_envelope

    envelope = to_envelope(ToolsInvalid("MODEL_OUTPUT_REJECTED"), "cor_a")
    assert envelope.code == "MODEL_OUTPUT_REJECTED"
    assert envelope.current_state is None
    assert envelope.supported_alternative is None


@pytest.mark.trace("T-E12-005", "I1", "I2")
@pytest.mark.parametrize(
    "changes", [{"origin_mode": "unknown"}, {"source_revision": 0}, {"generation": 0}, {"phase": "unknown"}]
)
def test_scope_rejects_invalid_controller_values(changes: dict[str, Any]) -> None:
    from dataclasses import replace

    from tests.tools.test_gateway import scope  # type: ignore[import-not-found]

    with pytest.raises(ToolsInvalid):
        replace(scope(), **changes)
