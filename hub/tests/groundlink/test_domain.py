from datetime import UTC, datetime, timedelta

import pytest

from crew_health.groundlink.domain.errors import BundleConflict, DemoSwitchInvalid
from crew_health.groundlink.domain.link import (
    DemoOverride,
    LinkState,
    ProfileLink,
    ScheduleWindow,
    next_change,
    resolve,
    split_delay_text,
    validate_demo,
    window_findings,
)
from crew_health.groundlink.domain.queue import Bundle, backoff_ms, check_same_content, to_arrive, to_send

AT = datetime(2026, 11, 14, tzinfo=UTC)


def profile(*windows: ScheduleWindow) -> ProfileLink:
    return ProfileLink("lunar_surface", 6, "delayed", "relay_outage", windows)


def state(p: ProfileLink, override: DemoOverride | None = None, at: datetime = AT) -> LinkState:
    return resolve(p, override, at, last_contact_at=None, delay_text="Delay.", delay_source_ids=())


def bundle(i: int, **changes: object) -> Bundle:
    from dataclasses import replace

    b = Bundle(
        f"glb_{i:04}",
        "ground_message",
        f"msg_{i}",
        "a" * 64,
        AT + timedelta(seconds=i),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        True,
        None,
    )
    return replace(b, **changes)  # type: ignore[arg-type]


@pytest.mark.trace("R49", "E29-01")
def test_schedule_golden_cases_and_contiguous_down_windows() -> None:
    a = ScheduleWindow(AT, AT + timedelta(hours=1), "down", "equipment", True)
    b = ScheduleWindow(a.ends_at, a.ends_at + timedelta(hours=1), "down", "relay_outage", False)
    p = profile(a, b)
    assert state(profile()).status == "delayed"
    assert state(profile()).next_contact_at is None
    down = state(p)
    assert down.status == "down" and down.next_contact_at == b.ends_at
    assert down.gap_kind == "equipment" and down.gap_planned
    assert down.one_way_delay_s == 6.0
    up = state(p, at=b.ends_at)
    assert (up.status, up.gap_kind, up.gap_planned, up.next_contact_at) == ("delayed", None, False, None)
    assert next_change(p, None, AT) == a.ends_at
    assert next_change(p, None, b.ends_at) is None


@pytest.mark.trace("R49", "E29-01")
def test_override_defaults_gap_and_keeps_delay_while_down() -> None:
    o = DemoOverride("down", None, None, None, False, AT)
    s = state(profile(), o)
    assert (s.source, s.gap_kind, s.gap_planned, s.one_way_delay_s) == (
        "demo_switch",
        "relay_outage",
        False,
        6.0,
    )
    assert next_change(profile(), o, AT) is None
    up = state(profile(), DemoOverride("up", 0, AT, "equipment", True, AT))
    assert (up.gap_kind, up.gap_planned, up.next_contact_at) == (None, False, None)


@pytest.mark.trace("R49")
@pytest.mark.parametrize(
    ("text", "expected", "ids"),
    [
        (
            "Near real time. The sources read give no delay figure for low Earth orbit; the ground team is close at hand.",
            "Near real time. The sources read give no delay figure for low Earth orbit; the ground team is close at hand.",
            (),
        ),
        (
            "Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay (EV01).",
            "Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay.",
            ("EV01",),
        ),
        (
            "Grows with distance from Earth. Up to 20 minutes one way near Mars (EV25).",
            "Grows with distance from Earth. Up to 20 minutes one way near Mars.",
            ("EV25",),
        ),
        ("Up to 20 minutes one way (EV25).", "Up to 20 minutes one way.", ("EV25",)),
        ("Keep (ordinary words) (S01, S01) , then (RT02).", "Keep (ordinary words), then.", ("S01", "RT02")),
    ],
)
def test_delay_source_groups_are_moved_without_changing_prose(
    text: str, expected: str, ids: tuple[str, ...]
) -> None:
    assert split_delay_text(text, set(ids)) == (expected, ids)


@pytest.mark.trace("R49", "E29-01")
@pytest.mark.parametrize(
    "window",
    [
        ScheduleWindow(AT, AT, "down", "unknown", False),
        ScheduleWindow(AT, AT + timedelta(hours=1), "down", None, False),
        ScheduleWindow(AT, AT + timedelta(hours=1), "up", "equipment", False),
        ScheduleWindow(AT, AT + timedelta(hours=1), "delayed", None, True),
    ],
)
def test_invalid_windows_name_their_index(window: ScheduleWindow) -> None:
    assert "window 0" in window_findings((window,))[0]


@pytest.mark.trace("R49", "E29-01")
def test_overlap_is_rejected_and_contiguous_windows_are_valid() -> None:
    a = ScheduleWindow(AT, AT + timedelta(hours=1), "down", "equipment", True)
    assert window_findings((a, a))
    assert not window_findings(
        (a, ScheduleWindow(a.ends_at, a.ends_at + timedelta(hours=1), "up", None, False))
    )


@pytest.mark.trace("R49")
@pytest.mark.parametrize(
    ("delay", "contact", "gap", "planned", "field"),
    [
        (-1.0, None, None, False, "/one_way_delay_s"),
        (float("inf"), None, None, False, "/one_way_delay_s"),
        (None, AT, None, False, "/next_contact_at"),
        (None, None, "equipment", False, "/gap_kind"),
        (None, None, None, True, "/gap_planned"),
    ],
)
def test_invalid_demo_fields_are_identified(
    delay: float | None, contact: datetime | None, gap: str | None, planned: bool, field: str
) -> None:
    with pytest.raises(DemoSwitchInvalid) as error:
        validate_demo("up", delay, contact, gap, planned)
    assert error.value.field == field


@pytest.mark.trace("R48", "R61")
def test_queue_is_bounded_ordered_and_retries_forever_with_capped_backoff() -> None:
    now = AT + timedelta(hours=1)
    items = (bundle(2), bundle(0), bundle(1))
    assert [b.ref_id for b in to_send(items, "up", now, 2)] == ["msg_0", "msg_1"]
    assert to_send(items, "down", now, 2) == ()
    waiting = bundle(3, state="sending", next_attempt_at=now + timedelta(seconds=1))
    assert to_send((waiting,), "delayed", now, 2) == ()
    assert to_send((waiting,), "delayed", now + timedelta(seconds=1), 2) == (waiting,)
    arrived = bundle(4, state="sent", arrival_due_at=now)
    assert to_arrive((arrived, waiting), now) == (arrived,)
    assert [backoff_ms(n, 100, 3, 500) for n in (1, 2, 3, 4, 10000)] == [100, 200, 400, 500, 500]
    check_same_content("a", "a")
    with pytest.raises(BundleConflict):
        check_same_content("a", "b")
