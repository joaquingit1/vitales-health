"""First start (SYSTEM 5.12): the plans release check asks the mission port for the mode while the hub has no active
release, so no configuration can be read. The identity answers need none and must say "not known", never fail."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.mission.adapters.live_state import LiveMissionState


class _Snapshots:
    @contextmanager
    def snapshot(self) -> Iterator[Any]:
        yield SimpleNamespace(release=SimpleNamespace(document=_no_document))


def _no_document(name: object) -> None:
    raise ProblemError(ErrorCode.CONFIG_INVALID, "no active release")


def _no_config(release: object) -> None:
    raise ProblemError(ErrorCode.CONFIG_INVALID, "no active release")


def _state() -> LiveMissionState:
    reader = SimpleNamespace(identity=lambda view: None)
    return LiveMissionState(reader, _Snapshots(), SimpleNamespace(), _no_config)  # type: ignore[arg-type]


def test_identity_answers_before_any_release_are_none_not_a_configuration_failure() -> None:
    state = _state()
    assert state.current() is None
    assert state.mission_mode() is None
    assert state.active_environment_id() is None


def test_a_label_still_needs_the_release_and_fails_closed_without_one() -> None:
    with pytest.raises(ProblemError) as caught:
        _state().inline_label_at(
            __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC)
        )
    assert caught.value.code == ErrorCode.CONFIG_INVALID
