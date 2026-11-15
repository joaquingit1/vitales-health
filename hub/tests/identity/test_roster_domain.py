"""E02 3 roster document checks: stable IDs, order, unique officer ranks, bounded Unicode names."""

from __future__ import annotations

from dataclasses import replace

import pytest
from e02_station import sim_bootstrap, sim_members, sim_station_principal
from hypothesis import given
from hypothesis import strategies as st

from crew_health.identity.domain.roster import (
    ProvisionedPrincipal,
    RosterMember,
    display_name,
    validate_bootstrap,
)
from crew_health.kernel.errors import ErrorCode, ProblemError


def check(
    *,
    members: tuple[RosterMember, ...] | None = None,
    principals: tuple[ProvisionedPrincipal, ...] | None = None,
) -> None:
    document = sim_bootstrap(members=members, principals=principals)
    validate_bootstrap(document, roster_max=64, day_span_max=366)


@pytest.mark.trace("T-E02-020", "R02")
def test_the_package_sim_roster_is_a_valid_document() -> None:
    document = validate_bootstrap(sim_bootstrap(), roster_max=64, day_span_max=366)
    assert [member.medical_officer_rank for member in document.members] == [None, 1, 2, None]


@pytest.mark.trace("T-E02-020")
def test_duplicate_ids_indexes_and_ranks_are_refused() -> None:
    mara, ilya = sim_members()[0], sim_members()[1]
    for members in (
        (mara, replace(ilya, person_id=mara.person_id)),
        (mara, replace(ilya, roster_index=mara.roster_index)),
        (replace(mara, medical_officer_rank=1), ilya),
        (replace(mara, medical_officer_rank=0),),
        (replace(mara, roster_index=-1),),
        (),
    ):
        with pytest.raises(ProblemError) as error:
            check(members=members)
        assert error.value.code in (ErrorCode.INVALID_VALUE, ErrorCode.MALFORMED_REQUEST)


@pytest.mark.trace("T-E02-020", "T-E02-009")
def test_principals_never_collide_with_people_or_impersonate_crew() -> None:
    station = sim_station_principal()
    for principals in (
        (replace(station, principal_id="mara"),),
        (replace(station, kind="crew"),),
        (station, replace(station, principal_id="stn_second")),
    ):
        with pytest.raises(ProblemError):
            check(principals=principals)
    adapters = (
        ProvisionedPrincipal("camera_1", "adapter", "Camera one"),
        ProvisionedPrincipal("camera_2", "adapter", "Camera two"),
    )
    check(principals=(station, *adapters))


@pytest.mark.trace("T-E02-020")
def test_roster_size_and_day_span_are_bounded() -> None:
    members = tuple(
        RosterMember(f"person_{index}", f"Person {index}", index, 1, None, None) for index in range(65)
    )
    with pytest.raises(ProblemError):
        check(members=members)
    with pytest.raises(ProblemError):
        check(members=(replace(sim_members()[0], on_roster_until_day=400),))
    with pytest.raises(ProblemError):
        check(members=(replace(sim_members()[0], on_roster_from_day=5, on_roster_until_day=4),))


@pytest.mark.trace("T-E02-020")
@given(name=st.text(min_size=1, max_size=64))
def test_display_names_are_bounded_unicode_without_control_characters(name: str) -> None:
    has_control = any(ord(c) < 32 or 127 <= ord(c) < 160 for c in name)
    fits = len(name.encode("utf-8")) <= 256 and bool(name.strip())
    if has_control or not fits:
        with pytest.raises(ProblemError):
            display_name(name, "name")
    else:
        assert display_name(name, "name") == name


@pytest.mark.trace("T-E02-020")
def test_long_multibyte_names_are_measured_in_bytes() -> None:
    assert display_name("Ж" * 128, "name")
    with pytest.raises(ProblemError):
        display_name("Ж" * 129, "name")
