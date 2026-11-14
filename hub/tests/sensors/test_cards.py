"""E23 section 12 tests T-E23-034, 037 to 040, 043, 046, 071 and the sentence part of 074.

Expected card words come from the package example `status-co2-station.json` and from CONTRACTS 12 and 13, not
from the configuration documents under test.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from e23_helpers import PRODUCT, channel_for, evaluation_input, point, sensor, utc
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.sensors.adapters.config_reader import SensorConfig
from crew_health.sensors.domain.card import (
    CardInput,
    ChannelView,
    LinkedWarning,
    compose_card,
    format_value,
    ground_delay_text,
    living_here,
    rank_key,
    select_card_channel,
    serve_return_option,
    shown_level,
)
from crew_health.sensors.domain.channel import ChannelFacts, Freshness
from crew_health.sensors.domain.evaluation import evaluate_limits
from crew_health.sensors.domain.reading import Instant
from crew_health.sensors.domain.sentences import (
    NOTHING_TO_DO,
    sentences_skipping,
    source_ids_in,
    split_sentences,
    strip_source_ids,
)

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None, suppress_health_check=list(HealthCheck))
UNSUPPORTED = "Not available here"  # ui-copy designed_states.not_available_here, passed in as configuration
END = utc(2014, 10, 7, 15, 36)


def view(
    config: SensorConfig,
    sensor_type_id: str,
    channel_id: str,
    values: list[str],
    *,
    interval: int | None,
    environment: str = "station_leo",
    source: str = "Replay, ISS 2014",
    location: str | None = None,
    freshness: Freshness = "current",
    note: str | None = None,
    step: timedelta = timedelta(minutes=5),
    end: datetime = END,
) -> ChannelView:
    rows = [point(end - step * (len(values) - 1 - n), v) for n, v in enumerate(values)]
    inp = evaluation_input(
        config, sensor_type_id, rows, interval=interval, environment=environment, freshness=freshness
    )
    channel: ChannelFacts = channel_for(
        config,
        sensor_type_id,
        channel_id=channel_id,
        interval=interval,
        source=source,
        location=location,
        environments=(environment,),
        note=note,
    )
    evaluation = evaluate_limits(inp)
    stale_since = end + timedelta(seconds=300) if freshness == "stale" else None
    return ChannelView(channel, evaluation, freshness, stale_since, "Trace: test provenance")


def card_input(
    config: SensorConfig,
    sensor_type_id: str,
    views: tuple[ChannelView, ...],
    *,
    environment: str = "station_leo",
    linked: tuple[LinkedWarning, ...] = (),
) -> CardInput:
    kind = sensor(config, sensor_type_id)
    hazard = config.hazard(kind.hazard_id)
    assert hazard is not None
    return CardInput(
        sensor_type=kind,
        environment_id=environment,
        channels=views,
        limits=config.limits,
        hazard=hazard,
        filters=config.effect_filters,
        linked_warnings=linked,
        unsupported_text=UNSUPPORTED,
        idle_origin="REPLAY",
        idle_source_label="Replay",
        evaluated_at=utc(2026, 11, 14),
    )


@pytest.mark.trace("T-E23-036", "T-E23-037", "R35", "CONTRACTS13", "V55")
def test_the_station_co2_card_matches_the_package_example(config: SensorConfig) -> None:
    expected = json.loads(
        (PRODUCT / "api/examples/sensors/status-co2-station.json").read_text(encoding="utf-8")
    )["value"]
    channel = view(
        config, "co2", "ch_co2_cabin_replay", ["3.9"] * 12, interval=300, source="Replay, ISS 2014"
    )
    card = compose_card(
        card_input(config, "co2", (channel,), linked=(LinkedWarning("wrn_co2_station_1", "act"),))
    )
    assert card.display_value.text == "3.9"  # the example card shows the 12 sample mean 3.5003 as 3.5
    assert (card.shown_level, card.reading_state, card.context_label) == (
        "act",
        "current",
        expected["context_label"],
    )
    assert [e.text for e in card.effects] == [e["text"] for e in expected["effects"]]
    assert [e.body_region for e in card.effects] == [e["body_region"] for e in expected["effects"]]
    assert list(card.todo) == expected["todo"]
    assert card.here == expected["here"]
    assert card.citation.text == expected["citation"]["text"]
    assert list(card.citation.source_ids) == expected["citation"]["source_ids"]
    mark = card.limit_marks[0]
    expected_mark = expected["limit_marks"][0]
    assert (mark.limit_id, mark.value, mark.unit, mark.comparator, mark.role, mark.label) == (
        expected_mark["limit_id"],
        Decimal(expected_mark["value"]),
        expected_mark["unit"],
        expected_mark["comparator"],
        expected_mark["role"],
        expected_mark["label"],
    )
    assert card.display_range == (Decimal(0), Decimal(5)) and card.linked_warning_ids == (
        "wrn_co2_station_1",
    )


@pytest.mark.trace("T-E23-036", "R35", "D16")
def test_the_golden_rr1_value_shows_as_3_5(config: SensorConfig, release: object, converter: object) -> None:
    from e23_helpers import assumed_pressure, read_rr1_ppm

    from crew_health.kernel.units import Unit
    from crew_health.sensors.domain.conversion import convert_wire_reading

    kind = sensor(config, "co2")
    rows = []
    for row in read_rr1_ppm():
        if row.at <= END:
            c = convert_wire_reading(
                converter,
                release,
                kind,
                value=row.value,
                unit="ppm",  # type: ignore[arg-type]
                canonical_unit=Unit("mm[Hg]"),
                pressure=assumed_pressure(),
            )
            rows.append(Instant(row.at, c.converted.canonical.value, c.converted.canonical.value))
    inp = evaluation_input(config, "co2", rows, interval=300)
    channel = ChannelView(
        channel_for(config, "co2", interval=300), evaluate_limits(inp), "current", None, None
    )
    card = compose_card(card_input(config, "co2", (channel,)))
    assert card.display_value.text == "3.5" and card.display_value.value == Decimal("3.5003")
    assert card.shown_level == "act" and card.evaluation.limit_id == "co2_1h_act"


@pytest.mark.trace("T-E23-037", "DE23-10", "AGENTS2")
def test_two_channels_disagree_worse_on_the_card_other_in_trace_no_averaging(config: SensorConfig) -> None:
    hot = view(
        config, "co2", "ch_a", ["3.9"] * 12, interval=300, source="Replay, ISS 2014", location="Columbus"
    )
    cool = view(config, "co2", "ch_b", ["2.0"] * 12, interval=300, source="Sim, test", location="Node 3")
    card = compose_card(card_input(config, "co2", (cool, hot)))
    assert card.channel_id == "ch_a" and card.display_value.text == "3.9"  # not the 2.95 average
    assert card.trace == "Trace: test provenance\nSim, test, Node 3: 2.0 mmHg"
    assert card.shown_level == "act"


@pytest.mark.trace("T-E23-037", "DE23-10")
def test_card_channel_ranking_levels_then_status_order_then_channel_id(config: SensorConfig) -> None:
    emergency = view(config, "noise", "ch_z", ["120"], interval=None)
    act = view(config, "noise", "ch_a", ["60"] * 1, interval=None)
    stale = view(config, "o2", "ch_stale", ["150"], interval=5, freshness="stale")
    ok_b = view(config, "o2", "ch_b", ["150"], interval=5)
    ok_a = view(config, "o2", "ch_a", ["150"], interval=5)
    assert select_card_channel((act, emergency)) is emergency
    assert select_card_channel((ok_b, stale, ok_a)) is stale  # stale outranks within_limit
    assert select_card_channel((ok_b, ok_a)) is ok_a  # ties by channel_id
    assert rank_key(emergency) < rank_key(act)
    assert select_card_channel(()) is None


@pytest.mark.trace("T-E23-034", "C13")
def test_a_display_only_limit_never_changes_the_card_status_or_shown_level(config: SensorConfig) -> None:
    calm = view(config, "air", "ch_air", ["0.5"], interval=None)
    card = compose_card(card_input(config, "air", (calm,)))
    assert card.evaluation.status == "within_limit" and card.shown_level is None
    assert card.shown_state_key == "within_limit_word"
    assert [m.limit_id for m in card.limit_marks] == ["air_index_info"]  # drawn as a mark only
    assert card.limit_marks[0].role == "display_only"


@pytest.mark.trace("T-E23-037", "C13")
def test_shown_level_is_the_highest_of_evaluation_and_linked_warnings(config: SensorConfig) -> None:
    assert shown_level(None, ()) is None
    assert shown_level("watch", (LinkedWarning("w1", "act"),)) == "act"
    assert shown_level("emergency", (LinkedWarning("w1", "act"),)) == "emergency"
    assert shown_level(None, (LinkedWarning("w1", "info"), LinkedWarning("w2", "watch"))) == "watch"
    quiet = view(config, "co2", "ch_q", ["2"] * 12, interval=300, freshness="stale")
    card = compose_card(card_input(config, "co2", (quiet,)))
    assert card.shown_state_key == "designed_states.sensor_quiet" and card.shown_level is None
    assert card.reading_state == "stale" and card.stale_since == END + timedelta(seconds=300)


@pytest.mark.trace("T-E23-037", "C13", "R42")
def test_a_type_with_no_channel_shows_no_reading_and_its_pending_limits(config: SensorConfig) -> None:
    card = compose_card(card_input(config, "rad", ()))
    assert card.reading_state == "no_reading" and card.channel_id is None
    assert card.display_value.text == "No reading" and card.display_value.value is None
    assert card.evaluation.pending_limit_ids == ("rad_rate_watch", "rad_rate_act")
    assert card.shown_state_key == "designed_states.no_reading"


@pytest.mark.trace("T-E23-038", "R41", "V73")
def test_perchlorate_salt_effect_shows_on_mars_only(config: SensorConfig) -> None:
    def effects(env: str) -> list[str]:
        channel = view(config, "dust", "ch_dust", ["0.01"] * 3, interval=60, environment=env)
        card = compose_card(card_input(config, "dust", (channel,), environment=env))
        return [e.text for e in card.effects]

    mars = effects("mars_surface")
    assert any(text.startswith("Martian dust is likely toxic: ") for text in mars)
    for other in ("lunar_surface", "station_leo", "transit"):
        shown = effects(other)
        assert not any(text.startswith("Martian dust is likely toxic: ") for text in shown)
        assert len(shown) == len(mars) - 1


@pytest.mark.trace("T-E23-039", "R41", "V73")
def test_an_effect_with_no_source_reads_the_unsupported_state_and_lights_no_region(
    config: SensorConfig,
) -> None:
    from dataclasses import replace

    base = card_input(config, "co2", (view(config, "co2", "ch_c", ["2"] * 12, interval=300),))
    effects = tuple(
        replace(e, source_ids=()) if index == 0 else e for index, e in enumerate(base.hazard.effects)
    )
    edited = replace(base, hazard=replace(base.hazard, effects=effects))
    card = compose_card(edited)
    assert card.effects[0].text == UNSUPPORTED and card.effects[0].body_region is None
    assert card.effects[1].text == "Shortness of breath"
    bare = replace(base, hazard=replace(base.hazard, source_ids=()))
    unsourced = compose_card(bare)
    assert unsourced.todo == (UNSUPPORTED,) and unsourced.here == UNSUPPORTED  # never guidance


@pytest.mark.trace("T-E23-040", "C12")
def test_ground_delay_text_comes_from_earth_delay_with_source_ids_moved_out(config: SensorConfig) -> None:
    delays = dict(config.earth_delay)
    text, ids = ground_delay_text(delays["lunar_surface"])
    assert (
        text
        == "Seconds. NASA gives 6 to 10 seconds on the surface of the Moon as an example of a small delay."
    )
    assert ids == ("EV01",)  # CONTRACTS 12 worked example
    assert ground_delay_text("Near real time.") == ("Near real time.", ())


@pytest.mark.trace("T-E23-043", "V58", "UC05")
def test_the_dose_rate_card_is_labeled_by_instrument_and_source_never_a_personal_dose(
    config: SensorConfig,
) -> None:
    channel = view(
        config,
        "rad",
        "ch_rad_columbus_replay",
        ["6.0"] * 4,
        interval=None,
        source="Replay, ISS 2022",
        location="Columbus module",
        note="DosTel1, Columbus module",
        step=timedelta(minutes=30),
        end=utc(2022, 4, 2, 1, 4, 40),
    )
    card = compose_card(card_input(config, "rad", (channel,)))
    assert card.context_label == "DosTel1, Columbus module"
    assert (card.source_label, card.origin_mode) == ("Replay, ISS 2022", "REPLAY")
    assert card.long_name == "Radiation dose rate" and card.display_value.unit == "µGy/h"
    assert (
        card.evaluation.status == "threshold_pending"
        and card.shown_state_key == "designed_states.threshold_pending"
    )
    dose_per_day = sensor(config, "rad_day")
    assert (
        dose_per_day.unit == "mSv/day" and card.display_value.unit != dose_per_day.unit
    )  # never a personal dose


@pytest.mark.trace("T-E23-046", "C12")
def test_return_option_is_served_unchanged_and_none_where_null() -> None:
    served = serve_return_option("A trip home (EV01) takes days.", ("EV01",), "flight surgeon")
    assert served is not None
    assert (served.trip_text, served.source_ids, served.to_be_set_by) == (
        "A trip home (EV01) takes days.",
        ("EV01",),
        "flight surgeon",
    )
    assert serve_return_option(None, (), None) is None


@pytest.mark.trace("T-E23-071", "CONTRACTS615", "R41")
def test_living_here_clauses_a_to_f_for_the_station(config: SensorConfig) -> None:
    ids = dict(config.environment_hazard_ids)["station_leo"]
    items = living_here(config.hazards, ids, "station_leo", config.effect_filters, UNSUPPORTED)
    # (a) condition hazards only, in the environment's order
    assert [i.hazard_id for i in items] == [
        "microgravity",
        "isolation_confinement",
        "circadian_light",
        "distance_autonomy",
    ]
    by_id = {i.hazard_id: i for i in items}
    # (b) the name without source IDs
    assert by_id["microgravity"].name == "Weightlessness (microgravity)"
    # (c) body regions of the effects
    assert by_id["microgravity"].body_regions[:3] == ("back", "flank", "legs")
    # (d) effects after filters, with their words
    assert by_id["microgravity"].effects[0].text.startswith("Bone loss: weight-bearing bone")
    # (e) info and watch sentences, skipping "Nothing to do" sentences, source IDs removed
    assert by_id["distance_autonomy"].todo == (
        "Note the time.",
        "Keep logging.",
        "Plan as if no answer will come soon.",
    )
    assert by_id["circadian_light"].todo[:2] == (
        "Keep regular sleep times.",
        "Avoid blue light before sleep.",
    )
    assert by_id["isolation_confinement"].todo == (
        "Take the private mood check.",
        "It is yours.",
        "Talk to someone you trust.",
        "Keep your sleep and exercise routine.",
    )
    # (f) the words for this environment
    assert by_id["isolation_confinement"].here and "(" not in by_id["isolation_confinement"].here
    lunar = living_here(
        config.hazards,
        dict(config.environment_hazard_ids)["lunar_surface"],
        "lunar_surface",
        config.effect_filters,
        UNSUPPORTED,
    )
    assert "partial_gravity" in [i.hazard_id for i in lunar]
    partial = next(i for i in lunar if i.hazard_id == "partial_gravity")
    assert partial.todo[0] == "Take the readjustment time before hard tasks."


# --- the sentence rule: T-E23-074 ------------------------------------------------------------------------


@pytest.mark.trace("T-E23-074", "CONTRACTS13", "V73")
def test_sentence_rule_examples() -> None:
    assert strip_source_ids("Limit applies (EV01). Also (EV01, C25) here.") == "Limit applies. Also here."
    assert source_ids_in("Limit applies (EV01). Also (EV01, C25) here. Not (an id) or (X1).") == (
        "EV01",
        "C25",
    )
    assert split_sentences('Tell the crew. Move now! Why? ok. 7 days. "Quote" ends.') == (
        "Tell the crew.",
        "Move now!",
        "Why? ok. 7 days.",
        '"Quote" ends.',
    )
    assert split_sentences("  ") == () and split_sentences("") == ()
    assert sentences_skipping("Nothing to do. Keep air moving (C15).") == ("Keep air moving.",)
    assert NOTHING_TO_DO == "Nothing to do"
    assert split_sentences("Wait. “Quoted.” Done.") == ("Wait.", "“Quoted.” Done.")


WORDS = st.sampled_from(["Tell", "the", "crew", "move", "now", "Check", "air", "7", "days", "ok", "Why"])
ENDINGS = st.sampled_from([".", "!", "?", ""])
GROUPS = st.sampled_from([" (EV01)", " (EV01, C25)", " (C25,N04)", ""])


@pytest.mark.trace("T-E23-074", "CONTRACTS13")
@given(
    parts=st.lists(
        st.tuples(st.lists(WORDS, min_size=1, max_size=5), GROUPS, ENDINGS), min_size=1, max_size=6
    ),
    pad=st.sampled_from([" ", "  ", " \t", "\n"]),
)
@EXAMPLES
def test_sentence_rule_properties(parts: list[tuple[list[str], str, str]], pad: str) -> None:
    text = pad.join(" ".join(words) + group + end for words, group, end in parts)
    cleaned = strip_source_ids(text)
    assert "(" not in cleaned and ")" not in cleaned
    assert cleaned == strip_source_ids(cleaned)  # idempotent
    assert "  " not in cleaned and cleaned == cleaned.strip()
    pieces = split_sentences(text)
    assert " ".join(pieces) == cleaned  # nothing lost, nothing added
    assert all(piece and piece == piece.strip() for piece in pieces)
    assert split_sentences(cleaned) == pieces


@pytest.mark.trace("T-E23-050", "CONTRACTS13", "D12")
def test_display_text_rounds_half_even_to_catalog_decimals_and_trims() -> None:
    assert format_value(Decimal("3.5003067"), 1, False) == "3.5"
    assert format_value(Decimal("6.0818949"), 1, True) == "6.1"
    assert format_value(Decimal("48.0"), 0, True) == "48"
    assert format_value(Decimal("48.0"), 1, True) == "48"
    assert format_value(Decimal("6.20"), 2, True) == "6.2"
    assert format_value(Decimal("0.25"), 1, False) == "0.2"  # half even
    assert format_value(Decimal("0.35"), 1, False) == "0.4"
    assert format_value(Decimal("-0.04"), 1, False) == "0.0"  # never a negative zero
    assert format_value(Decimal("1000000"), 0, True) == "1000000"
