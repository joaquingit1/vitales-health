"""E23 section 12 mission half of T-E23-091 that runs on fakes: local-only accounting of every mutation (S5.7).

Waiting on E01 T2 and a settled `mission/context/1` projection (replica schema, applier, cursor and revocation
tests): `replica.schema.json`, `adapters/replication.py` and the dependency-loss, grant-revocation and
cursor-crash cases of T-E23-091.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from e23m_helpers import START, Station, operator, person

from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.use_cases import (
    EventTimeFacts,
    act_on_change,
    create_overlay,
    propose_change,
    record_event,
)

pytestmark = pytest.mark.e23


@pytest.mark.trace("T-E23-091", "S5.7.1", "S8.3", "R-E23-20")
def test_every_mutation_is_accounted_locally_with_references_only_and_nothing_exports(
    station: Station,
) -> None:
    crew = person("per_a", "crew")
    with station.run(operator().actor) as uow:
        initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="node",
            environment_id="station_leo",
        )
    with station.run(operator().actor) as uow:
        proposal = propose_change(
            station.services,
            uow,
            operator(),
            environment_id="lunar_surface",
            reason="operator",
            effective_at=None,
        )
    with station.run(crew.actor) as uow:
        act_on_change(
            station.services,
            uow,
            crew,
            profile_id=proposal.profile_id,
            action="confirm",
            expected_revision=proposal.revision,
        )
    with station.run(crew.actor) as uow:
        create_overlay(
            station.services, uow, crew, kind="sleep", planned_start=None, started_at=START, location=None
        )
    with station.run(crew.actor) as uow:
        record_event(
            station.services,
            uow,
            crew,
            event_type="eva_planned",
            occurred_at=START + timedelta(hours=1),
            person_ids=("per_b",),
            location=None,
            note="private note",
            origin_mode="SIM",
            time=EventTimeFacts("known", START + timedelta(hours=1)),
        )
    changes = station.factory.state.replication
    assert {c.record_type for c in changes} == {"mission_environment", "mission_overlay", "mission_event"}
    assert all(c.export_projection is None for c in changes)  # only mission/context/1 may ever export
    assert all(set(json.loads(c.payload_json)) == {"id"} for c in changes)  # a safe reference, never fields
    text = " ".join(c.payload_json.decode() + c.audience.rule for c in changes)
    assert "per_a" not in text and "per_b" not in text and "private note" not in text
    assert all(c.audience.rule == "mission.local" and c.release_id == "rel_test_0001" for c in changes)
    appended = [c for c in changes if c.record_type == "mission_event"]
    assert [c.operation for c in appended] == ["append"]
    versions = [c.operation for c in changes if c.record_type == "mission_environment"]
    assert versions[0] == "create" and "version" in versions
