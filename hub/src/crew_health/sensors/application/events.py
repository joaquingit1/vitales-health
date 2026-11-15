"""Sensor wire events (E23 4.3, C18): `sensor.quiet`, `sensor.resumed` and the throttled `sensor.status_changed`.

Producer exports only: the bus payloads SensorEvaluated and ChannelFreshnessChanged for E24 wait on R-E23-5 and are
not published here. Payloads are the generated SensorChannel and SensorStatus models, never a private field.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, OutboxEvent, UnitOfWork

Scope = Literal["habitat", "operator"]


def emit(
    uow: UnitOfWork,
    kind: EventType,
    scopes: Sequence[Scope],
    object_ref: str,
    payload: BaseModel,
    environment_id: str | None,
) -> None:
    data = json.dumps(payload.model_dump(mode="json", exclude_unset=True), sort_keys=True).encode()
    audience = Audience("sensors.local", object_ref, AudienceProjectionKey("sensors", kind.value, 1), "1")
    for scope in scopes:
        uow.emit(
            OutboxEvent(
                kind,
                uow.now,
                EventScope(scope, None, environment_id),
                uow.release.release_id,
                uow.correlation_id,
                data,
                audience,
            )
        )
