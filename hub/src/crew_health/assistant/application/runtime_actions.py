from dataclasses import replace
from typing import Literal, cast

from crew_health.assistant.application.dto import RuntimeRecord
from crew_health.assistant.application.state_ports import State, View
from crew_health.assistant.domain.runtime import Runtime, RuntimeState, command, probe
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import UnitOfWork
from crew_health.model.application.ports import ModelRelease, Profile, RuntimeProbe

_TRANSIENT = frozenset({"model_loading", "model_unavailable", "probe_deadline", "probe_cancelled"})


class RuntimeActions:
    def __init__(self, state: State, *, profile: Profile, health_fail_threshold: int) -> None:
        if (
            profile not in {"flight", "demo", "eval"}
            or type(health_fail_threshold) is not int
            or health_fail_threshold < 1
        ):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "runtime settings invalid")
        self.state, self.profile, self.threshold = state, profile, health_fail_threshold

    def boot(self, uow: UnitOfWork) -> RuntimeRecord:
        current = self.state.runtime(uow)
        if current is None:
            current = RuntimeRecord(Runtime(False, RuntimeState.STOPPED), None, None, uow.now)
        elif current.runtime.desired:
            forbidden = (
                current.mode is None
                or current.model_release_id is None
                or current.mode == "stub"
                and self.profile == "flight"
            )
            current = replace(
                current,
                runtime=Runtime(True, RuntimeState.UNAVAILABLE if forbidden else RuntimeState.LOADING),
                since=uow.now,
                error_code=ErrorCode.CONFIG_INVALID if forbidden else None,
            )
        else:
            return current
        self.state.set_runtime(uow, current)
        return current

    def request(self, uow: UnitOfWork, *, start: bool, release: ModelRelease | None) -> RuntimeRecord:
        current = self.current(uow)
        if start and current.runtime.state in {RuntimeState.LOADING, RuntimeState.RUNNING}:
            return current
        wanted = command(current.runtime, start=start)
        if wanted == current.runtime:
            return current
        changed = replace(current, runtime=wanted, since=uow.now, error_code=None)
        if start:
            if release is None:
                raise ProblemError(ErrorCode.INVALID_TRANSITION, "model release not installed")
            doc = release.document
            ref, mode = doc.get("release_id"), doc.get("mode")
            if not isinstance(ref, str) or mode not in {"stub", "live"}:
                raise ProblemError(ErrorCode.CONFIG_INVALID, "model release invalid")
            require_id(ref)
            changed = replace(changed, model_release_id=ref, mode=cast(Literal["stub", "live"], mode))
            if mode == "stub" and self.profile == "flight":
                changed = replace(
                    changed,
                    runtime=Runtime(True, RuntimeState.UNAVAILABLE),
                    error_code=ErrorCode.CONFIG_INVALID,
                )
        self.state.set_runtime(uow, changed)
        return changed

    def apply_probe(
        self, uow: UnitOfWork, *, expected_version: int, result: RuntimeProbe, load_expired: bool
    ) -> RuntimeRecord | None:
        if self.state.runtime_version(uow) != expected_version:
            return None
        current = self.current(uow)
        valid = all(
            type(value) is bool
            for value in (result.healthy, result.identity_ok, result.adapters_empty, load_expired)
        )
        healthy = valid and result.healthy and result.identity_ok and result.adapters_empty
        fatal = not valid or not healthy and result.detail_code not in _TRANSIENT
        updated = probe(
            current.runtime,
            healthy=result.healthy if valid else False,
            identity_ok=healthy,
            worker_exited=fatal,
            load_expired=load_expired,
            threshold=self.threshold,
        )
        code = None
        if updated.state == RuntimeState.UNAVAILABLE:
            if current.runtime.state == RuntimeState.UNAVAILABLE:
                code = current.error_code
            elif result.detail_code == "model_configuration_invalid":
                code = ErrorCode.CONFIG_INVALID
            elif load_expired or result.detail_code == "model_load_timeout":
                code = ErrorCode.MODEL_TIMEOUT
            else:
                code = ErrorCode.MODEL_UNAVAILABLE
        changed = replace(
            current,
            runtime=updated,
            error_code=code,
            since=uow.now if updated.state != current.runtime.state else current.since,
        )
        if changed == current:
            return current
        return (
            changed if self.state.replace_runtime(uow, changed, expected_version=expected_version) else None
        )

    def current(self, view: View) -> RuntimeRecord:
        current = self.state.runtime(view)
        if current is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "assistant runtime not initialized")
        return current
