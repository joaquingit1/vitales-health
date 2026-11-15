from dataclasses import dataclass
from enum import StrEnum

from crew_health.assistant.domain.errors import AssistantRefused


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    LOADING = "loading"
    RUNNING = "running"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Runtime:
    desired: bool
    state: RuntimeState
    failed_probes: int = 0


def command(runtime: Runtime, *, start: bool) -> Runtime:
    if not start:
        return Runtime(False, RuntimeState.STOPPED)
    if runtime.state in (RuntimeState.RUNNING, RuntimeState.LOADING):
        return runtime
    return Runtime(True, RuntimeState.LOADING)


def probe(
    runtime: Runtime,
    *,
    healthy: bool,
    identity_ok: bool,
    worker_exited: bool,
    load_expired: bool,
    threshold: int,
) -> Runtime:
    if type(threshold) is not int or threshold < 1:
        raise AssistantRefused("invalid_runtime_budget")
    if not runtime.desired or runtime.state in (RuntimeState.STOPPED, RuntimeState.UNAVAILABLE):
        return runtime
    if worker_exited or (healthy and not identity_ok):
        return Runtime(True, RuntimeState.UNAVAILABLE)
    if runtime.state == RuntimeState.LOADING:
        if load_expired:
            return Runtime(True, RuntimeState.UNAVAILABLE)
        return Runtime(True, RuntimeState.RUNNING) if healthy and identity_ok else runtime
    if healthy and identity_ok:
        return Runtime(True, RuntimeState.RUNNING)
    failures = runtime.failed_probes + 1
    return Runtime(
        True, RuntimeState.UNAVAILABLE if failures >= threshold else RuntimeState.RUNNING, failures
    )
