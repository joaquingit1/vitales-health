from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from crew_health.config.application.ports import TextMatches
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.tools.application.policy_service import require_text_checks
from crew_health.tools.domain.arguments import JsonBounds
from crew_health.tools.domain.errors import ToolsInvalid

BOUNDS = JsonBounds(max_bytes=8192, max_depth=16, max_array_items=50, max_string_chars=2000)


@dataclass
class Rules:
    state: Literal["checked", "unavailable"] = "checked"
    amount: bool = False
    medicine: bool = False
    pin: str = "release"
    storage_failure: bool = False

    def find_amounts(self, text: str, *, release: ReleaseHandle) -> TextMatches:
        if self.storage_failure:
            raise StorageUnavailable("disk_full")
        return TextMatches(self.pin, self.state, ((0, 1),) if self.amount else ())

    def find_medicines(self, text: str, *, release: ReleaseHandle) -> TextMatches:
        return TextMatches(self.pin, self.state, ((0, 1),) if self.medicine else ())


@pytest.mark.trace("T-E12-060", "R56", "I2")
@pytest.mark.parametrize(
    "rules", [None, Rules(state="unavailable"), Rules(amount=True), Rules(medicine=True), Rules(pin="other")]
)
def test_text_boundary_fails_closed(rules: Rules | None) -> None:
    with pytest.raises(ToolsInvalid):
        require_text_checks(
            b'{"nested":[{"reason":"text"}]}',
            bounds=BOUNDS,
            rules=rules,
            release=MemoryRelease("release", {}),
        )


@pytest.mark.trace("T-E12-060", "I2")
def test_all_nested_strings_go_through_both_shared_checks() -> None:
    class RecordingRules(Rules):
        def __init__(self) -> None:
            super().__init__()
            self.amount_texts: list[str] = []
            self.medicine_texts: list[str] = []

        def find_amounts(self, text: str, *, release: ReleaseHandle) -> TextMatches:
            self.amount_texts.append(text)
            return super().find_amounts(text, release=release)

        def find_medicines(self, text: str, *, release: ReleaseHandle) -> TextMatches:
            self.medicine_texts.append(text)
            return super().find_medicines(text, release=release)

    rules = RecordingRules()
    require_text_checks(
        b'{"nested":[{"reason":"text"}]}', bounds=BOUNDS, rules=rules, release=MemoryRelease("release", {})
    )
    assert sorted(rules.amount_texts) == ["nested", "reason", "text"]
    assert sorted(rules.medicine_texts) == ["nested", "reason", "text"]


@pytest.mark.trace("T-E12-060", "I5")
def test_shared_text_storage_failure_is_never_a_policy_success() -> None:
    with pytest.raises(StorageUnavailable):
        require_text_checks(
            b'{"reason":"text"}',
            bounds=BOUNDS,
            rules=Rules(storage_failure=True),
            release=MemoryRelease("release", {}),
        )


@pytest.mark.trace("T-E12-050", "T-E12-060", "T-E12-062", "R56", "V63", "V84", "I3")
def test_prose_requires_exact_owner_authored_content_and_pin() -> None:
    from crew_health.kernel.testing import MemorySettings
    from crew_health.tools.application.policy_service import OutputPolicy
    from crew_health.tools.application.ports import TextField, TurnData
    from crew_health.tools.settings import setting_specs
    from tests.tools.test_bounds import settings_values  # type: ignore[import-not-found]
    from tests.tools.test_gateway import scope  # type: ignore[import-not-found]

    field = TextField("text", "source content", "step_a")
    turn = TurnData(MemoryRelease("cfg_a", {}), 1, (field,))
    rules = Rules(pin="cfg_a")
    policy = OutputPolicy(rules, MemorySettings(setting_specs(), settings_values()))
    assert policy.check((field,), scope(), turn).allowed
    for unsupported in (
        TextField("text", "your pulse was 120, above the limit", None),
        TextField("text", "invented threshold", "step_a"),
        TextField("text", "source content", "wrong_step"),
    ):
        assert not policy.check((unsupported,), scope(), turn).allowed
    assert not policy.check((field,), scope(), TurnData(MemoryRelease("other", {}), 1, (field,))).allowed


@pytest.mark.trace("T-E12-060", "R56")
def test_model_prose_passes_only_the_deterministic_rules() -> None:
    from dataclasses import replace

    from crew_health.kernel.testing import MemorySettings
    from crew_health.tools.application.policy_service import OutputPolicy
    from crew_health.tools.application.ports import TextField, TurnData
    from crew_health.tools.settings import setting_specs
    from tests.tools.test_bounds import settings_values
    from tests.tools.test_gateway import scope

    prose = TextField("reason", "Your pulse I can measure. Your rhythm I can't: that needs an ECG.", None)
    turn = TurnData(MemoryRelease("cfg_a", {}), 1, ())
    settings = MemorySettings(setting_specs(), settings_values())
    assert OutputPolicy(Rules(pin="cfg_a"), settings).check((prose,), scope(), turn).allowed
    for rules in (Rules(pin="cfg_a", amount=True), Rules(pin="cfg_a", medicine=True)):
        assert not OutputPolicy(rules, settings).check((prose,), scope(), turn).allowed
    policy = OutputPolicy(Rules(pin="cfg_a"), settings)
    assert not policy.check((replace(prose, text="Your pulse was 1 beat fast"),), scope(), turn).allowed
    replay = replace(scope(), origin_mode="REPLAY")
    assert not policy.check((prose,), replay, turn).allowed


@pytest.mark.trace("T-E12-060", "I7")
def test_unbound_text_checks_remain_unavailable() -> None:
    from crew_health.kernel.testing import MemorySettings
    from crew_health.tools.application.policy_service import OutputPolicy
    from crew_health.tools.application.ports import TextField, TurnData
    from crew_health.tools.settings import setting_specs
    from tests.tools.test_bounds import settings_values
    from tests.tools.test_gateway import scope

    field = TextField("text", "source content", "step_a")
    policy = OutputPolicy(None, MemorySettings(setting_specs(), settings_values()))
    verdict = policy.check((field,), scope(), TurnData(MemoryRelease("cfg_a", {}), 1, (field,)))
    assert not verdict.allowed
    assert verdict.error_code == "CAPABILITY_UNAVAILABLE"
