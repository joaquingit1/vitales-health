"""UC-M0: mission initialization (E23 3.3). One unit of work creates the identity and the first profile."""

from __future__ import annotations

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import UnitOfWork
from crew_health.mission.application.ports import OriginMode
from crew_health.mission.application.queries import MissionIdentity, profile_model
from crew_health.mission.application.use_cases import (
    Caller,
    Services,
    actor_ref,
    emit,
    note_audit,
    put_profile,
)
from crew_health.mission.domain import profile as profile_rules
from crew_health.mission.domain.errors import InvalidTransition, InvalidValue, UnauthorizedScope
from crew_health.mission.domain.profile import ProfileRecord


def initialize_mission(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    mission_mode: OriginMode,
    planned_length_days: int | None,
    site_node_id: str,
    environment_id: str,
) -> ProfileRecord:
    """Create mission_identity and the first active profile (reason mission_start). Refuses a second init.

    The mode is immutable for the mission. Activation quiet watches are the sensors participant's call; the
    demo registry validation joins this unit of work through that participant (T3, seam to be agreed).
    """
    if caller.actor.kind != "operator":
        raise UnauthorizedScope("mission init is an operator command")
    if services.store.identity(uow) is not None:
        raise InvalidTransition("the mission is already initialized")
    if planned_length_days is not None and planned_length_days < 1:
        raise InvalidValue("planned_length_days")
    config = services.config_of(uow.release)
    profile = config.profile(environment_id)
    if profile is None:
        raise InvalidValue("no profile for the environment")
    mission_id = services.ids.new("msn")
    services.store.create_identity(
        uow, MissionIdentity(mission_id, mission_mode, planned_length_days, site_node_id, uow.now)
    )
    record = profile_rules.start_profile(
        profile_id=services.ids.new("env"),
        template=profile.template,
        actor=actor_ref(caller.actor),
        now=uow.now,
        confirmations_required=config.confirmations_required,
        release_id=uow.release.release_id,
        lists=services.lists_of(uow, config, environment_id),
    )
    put_profile(services, uow, record)
    model = profile_model(record, is_crew=False, person_id=None)
    emit(
        uow,
        EventType.ENVIRONMENT_ACTIVATED,
        ("crew", "ground", "operator"),
        record.profile_id,
        model,
        environment_id=environment_id,
    )
    note_audit(
        uow, services.ids, "mission.initialized", {"mission_id": mission_id, "profile_id": record.profile_id}
    )
    for participant in services.participants():
        participant.on_activated(uow, model)
    return record
