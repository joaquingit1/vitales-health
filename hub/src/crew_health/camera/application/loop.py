"""The camera link's background loop: every `workers.camera.health_ms` it probes the worker, reconciles the
acquisitions it accepted (M2) and runs the camera's E01 tasks (M6). Started from the module binding only when
`workers.camera.enabled` is true; tests call `tick` directly. Until the worker first answers, it probes every
`workers.camera.startup_probe_ms` instead, at most `startup_probes` times, so readiness follows the worker's start
rather than one health interval later; a probe is the only thing that makes the camera ready. The loop never touches safety paths: a dead worker only
leaves camera readiness unknown and its requests "Not measured" with the reason (SYSTEM 3, AGENTS 1.3)."""

from __future__ import annotations

import logging
import threading

from crew_health.camera.application.dispatch import Dispatcher
from crew_health.camera.application.link import WorkerLink
from crew_health.camera.application.tasks import CameraTasks

_LOG = logging.getLogger("crew_health.camera")


class CameraLoop:
    def __init__(
        self,
        link: WorkerLink,
        dispatcher: Dispatcher,
        tasks: CameraTasks,
        *,
        interval_ms: int,
        startup_probe_ms: int | None = None,
        startup_probes: int = 0,
    ) -> None:
        self._link, self._dispatcher, self._tasks = link, dispatcher, tasks
        self._interval = interval_ms / 1000
        self._startup = None if startup_probe_ms is None else startup_probe_ms / 1000
        self._startup_probes = startup_probes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> int:
        """One pass; returns how many camera tasks ran."""
        self._link.probe()
        self._dispatcher.reconcile()
        return self._tasks.drain()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="camera-link", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def stop_within(self, timeout_s: float) -> bool:
        """The hub's WorkerLifecycle stop (SYSTEM 5.12): ask the loop to stop, wait at most `timeout_s` seconds and
        report whether its thread ended."""
        self.stop()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_s)
        return not thread.is_alive()

    def _await_worker(self) -> None:
        """Startup: probe until the worker first answers, within the bounded number of startup probes."""
        if self._startup is None:
            return
        for _ in range(self._startup_probes):
            if self._stop.is_set() or self._link.probe() is not None:
                return
            self._stop.wait(self._startup)

    def _run(self) -> None:
        self._await_worker()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:  # logged loudly (type only, no record text), retried next pass
                _LOG.error("camera link pass failed: %s", type(error).__name__)
            self._stop.wait(self._interval)
