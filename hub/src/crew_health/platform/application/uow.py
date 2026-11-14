from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import TypeAdapter

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    UnitOfWork,
)
from crew_health.platform.domain.errors import fail


class EventBus:
    def __init__(self, settings: HubSettings) -> None:
        self.settings = settings
        self.handlers: dict[str, Callable[[UnitOfWork, DomainEvent], None]] = {}
        self.queue: list[DomainEvent] = []
        self.dispatching = False

    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None:
        if name in self.handlers or len(self.handlers) >= self.settings.integer("events.max_subscribers"):
            raise fail(ErrorCode.CONFIG_INVALID)

        def invoke(uow: UnitOfWork, value: DomainEvent) -> None:
            if isinstance(value, event_type):
                handler(uow, value)

        self.handlers[name] = invoke

    def publish(self, uow: UnitOfWork, event: DomainEvent) -> None:
        if len(self.queue) >= self.settings.integer("event_bus.max_events"):
            raise fail(ErrorCode.INTERNAL_ERROR)
        self.queue.append(event)
        if self.dispatching:
            return
        self.dispatching = True
        try:
            for _ in range(self.settings.integer("event_bus.max_events")):
                if not self.queue:
                    return
                current = self.queue.pop(0)
                for name in sorted(self.handlers):
                    self.handlers[name](uow, current)
            if self.queue:
                raise fail(ErrorCode.INTERNAL_ERROR)
        finally:
            self.queue.clear()
            self.dispatching = False


def codec[T](model: type[T]) -> TypeAdapter[T]:
    from datetime import timedelta

    from pydantic import TypeAdapter

    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.commands import AcceptedReceipt
    from crew_health.kernel.replication import ProjectionKey
    from crew_health.kernel.scheduler import AbsoluteDeadline, ElapsedDeadline
    from crew_health.kernel.units import Quantity
    from crew_health.kernel.uow import Audience

    adapter = TypeAdapter(model)
    adapter.rebuild(
        force=True,
        _types_namespace={
            "datetime": datetime,
            "timedelta": timedelta,
            "TimeInput": TimeInput,
            "CommandIdentity": CommandIdentity,
            "AcceptedReceipt": AcceptedReceipt,
            "ProjectionKey": ProjectionKey,
            "AbsoluteDeadline": AbsoluteDeadline,
            "ElapsedDeadline": ElapsedDeadline,
            "Quantity": Quantity,
            "Actor": Actor,
            "Audience": Audience,
        },
    )
    return adapter
