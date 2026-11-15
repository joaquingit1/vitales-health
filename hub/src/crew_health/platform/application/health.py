from datetime import datetime

from crew_health.kernel.clock import require_utc
from crew_health.kernel.scheduler import ComponentHealth, HealthSnapshot

OVERFLOW = "health.components_overflow"


class ComponentHealthState:
    """Per-component health, at most `maximum` named components (registry.max_hooks). A component beyond the bound
    is never dropped silently: it degrades the reserved overflow component instead."""

    def __init__(self, *, maximum: int) -> None:
        self.maximum = maximum
        self._components: dict[str, ComponentHealth] = {}

    def _key(self, component: str) -> str:
        if component in self._components or len(self._components) < self.maximum:
            return component
        return OVERFLOW

    def mark_degraded(self, component: str, code: str, since: datetime) -> None:
        require_utc(since)
        key = self._key(component)
        self._components[key] = ComponentHealth(key, code, since)

    def mark_ok(self, component: str) -> None:
        key = self._key(component)
        if key != OVERFLOW:
            self._components[key] = ComponentHealth(key, None, None)

    def snapshot(self) -> HealthSnapshot:
        components = tuple(self._components[key] for key in sorted(self._components))
        return HealthSnapshot("degraded" if any(c.code is not None for c in components) else "ok", components)
