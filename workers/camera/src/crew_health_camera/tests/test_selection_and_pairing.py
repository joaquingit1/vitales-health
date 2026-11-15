"""E06-02: best-view selection keeps every source and the disagreement (R11, V07); explicit pairing discards
unpaired and ambiguous frames (R29, V08, V39)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_camera.domain.analysis import NoReading, Reading
from crew_health_camera.domain.features import FrameFeatures
from crew_health_camera.domain.pairing import Pairing, admission, admit
from crew_health_camera.domain.selection import Refusal, Selection, select


def reading(device: str, value: int, snr: float) -> Reading:
    return Reading("pulse", device, Decimal(value), "/min", snr, 600)


@pytest.mark.trace("R11", "V07", "E06-02")
def test_disagreeing_views_are_all_kept_and_the_best_signal_wins() -> None:
    result = select("pulse", [reading("cam_2", 96, 9.0), reading("cam_1", 104, 4.0)], 2.0)
    assert isinstance(result, Selection)
    assert result.selected.device_id == "cam_2" and result.selected.value == Decimal(96)
    assert [r.device_id for r in result.others] == ["cam_1"]
    assert (result.disagree, result.spread, result.reason) == (True, Decimal(8), "highest_signal_quality")


@pytest.mark.trace("R11", "E06-02")
def test_agreeing_views_tie_break_on_device_id_and_report_no_conflict() -> None:
    result = select("pulse", [reading("cam_3", 95, 5.0), reading("cam_1", 96, 5.0)], 2.0)
    assert isinstance(result, Selection)
    assert result.selected.device_id == "cam_1" and not result.disagree


@pytest.mark.trace("R11", "E06-02")
def test_one_camera_is_enough_and_refused_views_stay_listed() -> None:
    refused = NoReading("pulse", "cam_2", "too_much_movement")
    result = select("pulse", [refused, reading("cam_1", 70, 3.0)], 2.0)
    assert isinstance(result, Selection)
    assert result.reason == "only_qualified_view" and result.refused == (refused,)


@pytest.mark.trace("V05", "E06-02")
def test_no_qualified_view_is_a_refusal_with_every_reason() -> None:
    a, b = NoReading("pulse", "cam_2", "too_dark"), NoReading("pulse", "cam_1", "too_much_movement")
    assert select("pulse", [a, b], 2.0) == Refusal("pulse", "too_much_movement", (b, a))
    assert select("pulse", [], 2.0) == Refusal("pulse", "no_camera", ())


@pytest.mark.trace("V07", "E06-02")
@given(st.lists(st.tuples(st.integers(40, 180), st.floats(-20, 40)), min_size=1, max_size=4))
def test_selection_never_averages(views: list[tuple[int, float]]) -> None:
    readings = [reading(f"cam_{i}", v, s) for i, (v, s) in enumerate(views)]
    result = select("pulse", readings, 2.0)
    assert isinstance(result, Selection)
    kept = (result.selected, *result.others)
    assert sorted(r.device_id for r in kept) == sorted(r.device_id for r in readings)
    assert result.selected.value in {r.value for r in readings}
    assert result.disagree == (max(v for v, _ in views) - min(v for v, _ in views) > 2)


def frame(device: str, t_ns: int, *, faces: int = 1, segment: str = "seg_1") -> FrameFeatures:
    return FrameFeatures(device, segment, t_ns, 120.0, 0.0, faces, None, None)


PAIRED = Pairing("acq_1", "person_a", frozenset({"cam_1"}), "seg_1", 10_000, False)


@pytest.mark.trace("R29", "V08", "E06-02")
def test_frames_from_an_unpaired_camera_are_discarded() -> None:
    admitted = admit(PAIRED, [frame("cam_1", 1), frame("cam_9", 2)])
    assert [f.device_id for f in admitted.frames["cam_1"]] == ["cam_1"]
    assert "cam_9" not in admitted.frames and admitted.discarded["cam_9"] == {"not_paired": 1}


@pytest.mark.trace("R29", "V08", "V39", "E06-02")
@pytest.mark.parametrize(
    ("pairing", "item", "reason"),
    [
        (PAIRED, frame("cam_1", 5, faces=2), "ambiguous_subject"),
        (PAIRED, frame("cam_1", 20_000), "pairing_expired"),
        (PAIRED, frame("cam_1", 5, segment="seg_2"), "pairing_expired"),
        (replace(PAIRED, revoked=True), frame("cam_1", 5), "pairing_revoked"),
        (PAIRED, frame("cam_1", 5, faces=0), None),
    ],
)
def test_ambiguous_stale_and_revoked_frames_are_discarded(
    pairing: Pairing, item: FrameFeatures, reason: str | None
) -> None:
    assert admission(pairing, item) == reason


@pytest.mark.trace("R29", "V08", "E06-02")
def test_a_camera_with_no_admitted_frame_reports_the_most_decisive_reason() -> None:
    two = replace(PAIRED, device_ids=frozenset({"cam_1", "cam_2"}))
    admitted = admit(two, [frame("cam_1", 1), frame("cam_1", 20_000), frame("cam_2", 2)])
    assert admitted.reason_for("cam_1") is None
    stale = admit(two, [frame("cam_1", 20_000), frame("cam_1", 1, segment="seg_2"), frame("cam_2", 2)])
    assert stale.reason_for("cam_1") == "pairing_expired"
    assert admitted.reason_for("cam_2") is None
    assert admit(two, []).reason_for("cam_1") == "no_camera"


@pytest.mark.trace("R29", "V08", "E06-02", "M5")
def test_a_second_face_on_and_off_makes_the_whole_window_ambiguous() -> None:
    """A crewmate leaning in for some frames: the single-face frames around them may show either person, so the
    camera gives the patient nothing, however many single-face frames remain."""
    frames = [frame("cam_1", t, faces=2 if t % 30 == 0 else 1) for t in range(1, 300)]
    admitted = admit(PAIRED, frames)
    assert admitted.frames["cam_1"] == ()  # no single-face frame of that window reaches any analysis
    assert admitted.discarded["cam_1"]["ambiguous_subject"] == len(frames)
    assert admitted.reason_for("cam_1") == "ambiguous_subject"
