from __future__ import annotations

from threading import Event, Thread

from crew_health.assistant.adapters.calls import BoundedCalls
from crew_health.assistant.application.runtime_actions import RuntimeActions
from crew_health.assistant.application.state_ports import State
from crew_health.assistant.domain.runtime import RuntimeState
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.model.application.ports import ModelRuntimePort, RuntimeProbe


class NeverCancel:
    def is_cancelled(self) -> bool:
        return False


class RuntimeWorker:
    def __init__(
        self,
        *,
        state: State,
        actions: RuntimeActions,
        uows: UnitOfWorkFactory,
        views: ReadSnapshotFactory,
        clock: Clock,
        model: ModelRuntimePort,
        calls: BoundedCalls,
        health: HealthState,
        actor: Actor,
        interval_ms: int,
        probe_ms: int,
        load_ms: int,
    ) -> None:
        (
            self.state,
            self.actions,
            self.uows,
            self.views,
            self.clock,
            self.model,
            self.calls,
            self.health,
            self.actor,
        ) = state, actions, uows, views, clock, model, calls, health, actor
        self.interval, self.probe_ms, self.load_ms = interval_ms / 1000, probe_ms, load_ms
        self.closed, self.wake = Event(), Event()
        self.thread: Thread | None = None
        self.started_release: str | None = None
        self.loading: tuple[str, int] | None = None

    def notify(self) -> None:
        self.wake.set()

    def start(self) -> None:
        if self.thread is None:
            self.thread = Thread(target=self.run, name="assistant-runtime", daemon=True)
            self.thread.start()

    def stop(self, timeout_s: float) -> bool:
        self.closed.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout_s)
        return self.thread is None or not self.thread.is_alive()

    def run(self) -> None:
        try:
            while not self.closed.is_set():
                self.wake.clear()
                try:
                    self.tick()
                    self.health.mark_ok("assistant.runtime")
                except Exception:
                    self.health.mark_degraded("assistant.runtime", "MODEL_UNAVAILABLE", self.clock.now())
                self.wake.wait(self.interval)
        finally:
            if self.started_release is not None:
                try:
                    self.model.stop()
                except Exception:
                    self.health.mark_degraded("assistant.runtime", "MODEL_UNAVAILABLE", self.clock.now())

    def tick(self) -> None:
        with self.views.snapshot() as view:
            current = self.actions.current(view)
            version = self.state.runtime_version(view)
        if not current.runtime.desired or current.runtime.state == RuntimeState.UNAVAILABLE:
            if self.started_release is not None:
                self.calls.invoke(self.model.stop, budget_ns=self.probe_ms * 1_000_000, cancel=NeverCancel())
                self.started_release = None
            return
        if current.model_release_id is None:
            result = RuntimeProbe(False, False, False, "model_configuration_invalid")
        else:
            try:
                if self.started_release != current.model_release_id:
                    self.loading = (self.clock.process_epoch(), self.clock.monotonic_ns())
                    self.calls.invoke(
                        lambda: self.model.start(current.model_release_id or ""),
                        budget_ns=self.load_ms * 1_000_000,
                        cancel=NeverCancel(),
                    )
                    self.started_release = current.model_release_id
                result = self.calls.invoke(
                    lambda: self.model.probe(timeout_s=self.probe_ms / 1000),
                    budget_ns=self.probe_ms * 1_000_000,
                    cancel=NeverCancel(),
                )
            except ProblemError:
                result = RuntimeProbe(
                    False,
                    False,
                    False,
                    "model_configuration_invalid" if self.started_release is None else "model_unavailable",
                )
        expired = False
        if current.runtime.state == RuntimeState.LOADING and self.loading is not None:
            epoch, start = self.loading
            now = self.clock.monotonic_ns()
            expired = (
                epoch != self.clock.process_epoch() or now < start or now - start >= self.load_ms * 1_000_000
            )
        with self.uows.begin(actor=self.actor, correlation_id=self.actor.id) as uow:
            accepted = self.actions.apply_probe(
                uow, expected_version=version, result=result, load_expired=expired
            )
        if accepted is None and self.started_release is not None:
            self.calls.invoke(self.model.stop, budget_ns=self.probe_ms * 1_000_000, cancel=NeverCancel())
            self.started_release = None
