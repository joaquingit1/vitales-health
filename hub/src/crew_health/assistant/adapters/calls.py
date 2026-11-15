from collections.abc import Callable
from concurrent.futures import Future
from threading import BoundedSemaphore, Event, Thread

from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.model.application.ports import CancelToken


class BoundedCalls:
    def __init__(self, clock: Clock, *, max_inflight: int, max_duration_ms: int, cancel_poll_ms: int) -> None:
        if (
            any(
                type(value) is not int or value < 1
                for value in (max_inflight, max_duration_ms, cancel_poll_ms)
            )
            or cancel_poll_ms > max_duration_ms
        ):
            raise ProblemError(ErrorCode.CONFIG_INVALID, "assistant call bounds invalid")
        self.clock, self.ceiling, self.poll = clock, max_duration_ms * 1_000_000, cancel_poll_ms / 1000
        self.slots = BoundedSemaphore(max_inflight)

    def invoke[T](self, fn: Callable[[], T], *, budget_ns: int, cancel: CancelToken) -> T:
        epoch, started = self.clock.process_epoch(), self.clock.monotonic_ns()
        if type(budget_ns) is not int or budget_ns < 1:
            raise ProblemError(ErrorCode.MODEL_TIMEOUT, "assistant dependency deadline expired")
        deadline = started + min(budget_ns, self.ceiling)
        self._check(cancel, epoch, started, deadline)
        if not self.slots.acquire(blocking=False):
            raise ProblemError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "assistant dependency capacity unavailable", retryable=True
            )
        result: Future[T] = Future()
        done = Event()

        def run() -> None:
            try:
                result.set_result(fn())
            except BaseException as error:
                result.set_exception(error)
            finally:
                self.slots.release()
                done.set()

        try:
            Thread(target=run, name="assistant_dependency", daemon=True).start()
        except RuntimeError:
            self.slots.release()
            raise ProblemError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "assistant dependency capacity unavailable", retryable=True
            ) from None
        while not done.is_set():
            self._check(cancel, epoch, started, deadline)
            left = max(0, deadline - self.clock.monotonic_ns()) / 1_000_000_000
            done.wait(min(self.poll, left))
        self._check(cancel, epoch, started, deadline)
        try:
            return result.result()
        except ProblemError:
            raise
        except BaseException:
            raise ProblemError(
                ErrorCode.INTERNAL_ERROR, "assistant dependency failed", retryable=True
            ) from None

    def _check(self, cancel: CancelToken, epoch: str, started: int, deadline: int) -> None:
        if cancel.is_cancelled():
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "assistant call cancelled")
        now = self.clock.monotonic_ns()
        if self.clock.process_epoch() != epoch or now < started or now >= deadline:
            raise ProblemError(ErrorCode.MODEL_TIMEOUT, "assistant dependency deadline expired")
