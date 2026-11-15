from datetime import datetime

from crew_health.kernel.clock import require_utc
from crew_health.kernel.scheduler import ComponentHealth, HealthSnapshot


class ComponentHealthState:
    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}

    def mark_degraded(self, component: str, code: str, since: datetime) -> None:
        require_utc(since)
        self._components[component] = ComponentHealth(component, code, since)

    def mark_ok(self, component: str) -> None:
        self._components[component] = ComponentHealth(component, None, None)

    def snapshot(self) -> HealthSnapshot:
        components = tuple(self._components[key] for key in sorted(self._components))
        return HealthSnapshot("degraded" if any(c.code is not None for c in components) else "ok", components)
