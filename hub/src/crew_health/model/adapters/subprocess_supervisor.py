"""R-E17-1 development interim. Flight always requires E20's supervisor."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Literal

from crew_health.model.application.ports import LaunchSpec
from crew_health.model.domain.release import ModelRefused


class DevelopmentSupervisor:
    def __init__(self, *, profile: Literal["demo", "eval"], stop_grace_ms: int) -> None:
        if profile not in ("demo", "eval") or not 1 <= stop_grace_ms <= 5000:
            raise ModelRefused("development_supervisor_refused")
        self.grace = stop_grace_ms / 1000
        self.process: subprocess.Popen[bytes] | None = None

    def enable(self, process_id: str, spec: LaunchSpec) -> None:
        if process_id != "model" or spec.process_id != "model" or self.state(process_id) == "running":
            raise ModelRefused("process_start_refused")
        if not spec.argv or not 1 <= spec.cpu_budget <= 6 or not 1 <= spec.memory_budget_bytes <= 7500000000:
            raise ModelRefused("process_budget_refused")
        safe_environment = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "TMPDIR", "TEMP", "SYSTEMROOT")
        }
        safe_environment.update(spec.env)
        self.process = subprocess.Popen(
            spec.argv,
            cwd=spec.cwd,
            env=safe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        if sys.platform != "win32":
            os.setpriority(os.PRIO_PROCESS, self.process.pid, spec.nice)

    def disable(self, process_id: str) -> None:
        if process_id != "model":
            raise ModelRefused("process_stop_refused")
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.grace)
        self.process = None

    def state(self, process_id: str) -> str:
        if process_id != "model":
            raise ModelRefused("process_unknown")
        return "running" if self.process is not None and self.process.poll() is None else "stopped"
