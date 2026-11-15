"""SYSTEM 3: isolated daemon loop with visible failure counts."""

import threading

from crew_health.groundlink.application.agent import ContactAgent


class AgentLoop:
    def __init__(self, agent: ContactAgent, poll_ms: int) -> None:
        self.agent, self.poll_s = agent, poll_ms / 1000
        self.failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="groundlink-agent")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.agent.run_once()
            except Exception:
                self.failures += 1
            self._stop.wait(self.poll_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_s)
