"""Bounded in-process maintenance loop (SYSTEM 5.7, 5.10, 5.10.1, 5.10.2): runs the registered recovery steps
(scheduled jobs, durable subscribers, event retention, pending evaluations, task leases, clock sweep) one after
another, each in its own write transactions under the hub's single writer lock.

A failing step degrades only its own health component and is logged with allowlisted fields (step name, error
class and code); the other steps keep running. The loop has a fixed poll interval from hub settings and stops
cleanly on request."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings
from crew_health.platform.domain.errors import fail

LOGGER = "crew_health.platform.supervisor"


@dataclass(frozen=True, slots=True)
class SupervisorStep:
    """One bounded unit of maintenance. `run` opens its own transactions and returns how much work it did."""

    name: str
    run: Callable[[], int]

    def __post_init__(self) -> None:
        require_id(self.name)


class Supervisor:
    def __init__(
        self,
        *,
        steps: tuple[SupervisorStep, ...],
        settings: HubSettings,
        health: HealthState,
        now: Callable[[], datetime],
    ) -> None:
        names = [step.name for step in steps]
        if len(set(names)) != len(names) or len(steps) > settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self.steps, self.settings, self.health, self.now = steps, settings, health, now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, int | None]:
        """One pass over every step. Returns work done per step, None for a step that failed this pass."""
        results: dict[str, int | None] = {}
        for step in self.steps:
            component = "supervisor." + step.name
            try:
                results[step.name] = step.run()
            except Exception as error:  # isolation boundary: one step degrades only itself
                code = error.code.value if isinstance(error, ProblemError) else ErrorCode.INTERNAL_ERROR.value
                results[step.name] = None
                self.health.mark_degraded(component, code, self.now())
                logging.getLogger(LOGGER).error(
                    "supervisor step failed: step=%s error=%s code=%s", step.name, type(error).__name__, code
                )
                continue
            self.health.mark_ok(component)
        return results

    def _loop(self) -> None:
        interval = self.settings.integer("supervisor.poll_ms") / 1000
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(timeout=interval)

    def start(self) -> None:
        if self._thread is not None:
            raise fail(ErrorCode.INVALID_TRANSITION)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="crew-health-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to stop and wait for the pass in progress, bounded by the hub worker timeout."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self.settings.integer("workers.hub.timeout_ms") / 1000)
            if thread.is_alive():
                raise fail(ErrorCode.INTERNAL_ERROR)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
