"""The ActivationParticipant (E23 3.3 step 4, 3.4): what sensors do when mission activates an environment.

Called by mission after its synchronous handlers drain, in the same unit of work. It records the active environment
(no readings are evaluated before one exists), starts a separate quiet watch for every channel that has reported
(it never replaces D14's E or receive reference) and keeps an already stale channel stale. It publishes no
SensorEvaluated with an invented reading.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from crew_health.contracts_gen.schemas.environment_profile_schema import EnvironmentProfile
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.application.deadlines import refresh, schedule_deadline
from crew_health.sensors.application.registry import install
from crew_health.sensors.application.runtime import Runtime
from crew_health.sensors.application.store import EnvironmentRow


class SensorActivation:
    def __init__(self, rt: Runtime) -> None:
        self._rt = rt

    def on_activated(self, uow: UnitOfWork, profile: EnvironmentProfile) -> None:
        rt = self._rt
        facts = rt.mission.current(uow) if rt.mission is not None else None
        if facts is None:
            return  # no mission, no environment: sensors stay series only
        activated: datetime = profile.activated_at.root if profile.activated_at is not None else uow.now
        environment_id = profile.environment_id.root
        rt.store.save_environment(uow, EnvironmentRow(profile.profile_id.root, environment_id, facts.mission_mode, activated))
        self._demo(uow, facts.mission_mode)
        for channel in rt.store.channels(uow):
            if environment_id not in channel.facts.environment_ids or not channel.has_history:
                continue
            watched = replace(channel, quiet_anchor_at=uow.now, quiet_anchor_environment=environment_id)
            rt.store.save_channel(uow, watched)
            refresh(rt, uow, watched, cause="activation")
            schedule_deadline(rt, uow, watched, anchor=None, at=uow.now)
        uow.audit(AuditEntry(rt.ids.new("aud"), "sensors.environment_activated", 1, uow.actor, {"profile_id": profile.profile_id.root}, uow.correlation_id, None, {}))

    def _demo(self, uow: UnitOfWork, mission_mode: str) -> None:
        """The demo registry exists only in the demo profile and a SIM or REPLAY mission, loaded in this same unit
        of work with its hash audited (E23 5.3). Before mission init no demo channel exists."""
        rt = self._rt
        if rt.deployment_profile != "demo" or mission_mode not in ("SIM", "REPLAY"):
            return
        if not rt.demo_registry_path or rt.read_file is None:
            return
        install(rt, uow, rt.read_file(rt.demo_registry_path), source="demo")
