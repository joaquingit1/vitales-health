"""Bounded loopback SSE transport. Thinking never crosses this adapter's result boundary."""

from __future__ import annotations

import hashlib
import http.client
import queue
import socket
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

import jsonschema

from crew_health.model.application.ports import (
    CancelToken,
    Deadline,
    JsonValue,
    StepCancelled,
    StepMalformed,
    StepOk,
    StepProgress,
    StepRequest,
    StepResult,
    StepTimedOut,
    StepUnavailable,
    ToolCall,
    Usage,
)
from crew_health.model.application.prompt_builder import TokenCounter, request_bytes
from crew_health.model.domain.release import ModelRefused, object_value, strict_json


@dataclass(frozen=True, slots=True)
class ClientLimits:
    max_bytes: int
    max_chunks: int
    max_tools: int
    connect_timeout_s: float
    cancel_poll_s: float

    def __post_init__(self) -> None:
        if not (
            1 <= self.max_bytes <= 8388608
            and 1 <= self.max_chunks <= 65536
            and 1 <= self.max_tools <= 128
            and 0 < self.connect_timeout_s <= 10
            and 0 < self.cancel_poll_s <= 0.2
        ):
            raise ModelRefused("client_bounds_invalid")


@dataclass(slots=True)
class _State:
    content: str = ""
    thinking: str = ""
    calls: dict[int, dict[str, str]] = field(default_factory=dict)
    finish: str = ""
    input_tokens: int = 0
    first: bool = False


class LlamaClient:
    def __init__(self, *, port: int, api_key: str, tokens: TokenCounter, limits: ClientLimits) -> None:
        if not 1 <= port <= 65535 or not api_key or "\n" in api_key or "\r" in api_key:
            raise ModelRefused("client_configuration_invalid")
        self.port, self.api_key, self.tokens, self.limits = port, api_key, tokens, limits

    def run_step(
        self,
        request: StepRequest,
        *,
        deadline: Deadline,
        cancel: CancelToken,
        progress: Callable[[StepProgress], None],
    ) -> StepResult:
        if cancel.is_cancelled():
            return StepCancelled()
        if deadline.clock.monotonic_ns() >= deadline.total_ns:
            return StepTimedOut("total")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.limits.connect_timeout_s)
        result: queue.Queue[StepResult] = queue.Queue(maxsize=1)
        updates: queue.Queue[StepProgress] = queue.Queue(maxsize=1)
        first = threading.Event()
        active_socket: list[socket.socket] = []
        stopped = threading.Event()

        def run() -> None:
            try:
                value = self._exchange(connection, request, deadline, updates, first, stopped, active_socket)
            except OSError, http.client.HTTPException, ValueError, KeyError, TypeError, RecursionError:
                value = StepUnavailable("model_transport_failed")
            finally:
                connection.close()
            try:
                result.put_nowait(value)
            except queue.Full:
                pass

        thread = threading.Thread(target=run, name="model-step", daemon=True)
        thread.start()
        try:
            # Each loop consumes elapsed time; iteration count also bounded by the supplied total deadline.
            remaining = max(0, deadline.total_ns - deadline.clock.monotonic_ns()) / 1e9
            iterations = max(1, int(remaining / self.limits.cancel_poll_s) + 2)
            for _ in range(iterations):
                try:
                    progress(updates.get_nowait())
                except queue.Empty:
                    pass
                if cancel.is_cancelled():
                    return StepCancelled()
                now = deadline.clock.monotonic_ns()
                if now >= deadline.total_ns:
                    return StepTimedOut("total")
                if not first.is_set() and now >= deadline.first_token_ns:
                    return StepTimedOut("first_token")
                try:
                    return result.get(timeout=min(self.limits.cancel_poll_s, (deadline.total_ns - now) / 1e9))
                except queue.Empty:
                    continue
            return StepTimedOut("total")
        finally:
            stopped.set()
            # Shutdown, not just close, interrupts a blocked reader and frees the server's generation slot.
            if active_socket:
                try:
                    active_socket[0].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
            thread.join(timeout=self.limits.cancel_poll_s)

    def _exchange(
        self,
        connection: http.client.HTTPConnection,
        request: StepRequest,
        deadline: Deadline,
        updates: queue.Queue[StepProgress],
        first: threading.Event,
        stopped: threading.Event,
        active_socket: list[socket.socket],
    ) -> StepResult:
        raw = request_bytes(request.bundle, request.release_id)
        if hashlib.sha256(raw).hexdigest() != request.bundle.prompt_sha256:
            return StepMalformed("request_fingerprint_mismatch", hashlib.sha256(b"").hexdigest())
        if len(raw) > self.limits.max_bytes:
            return StepUnavailable("prompt_too_large")
        connection.connect()
        if connection.sock is not None:
            active_socket.append(connection.sock)
        if stopped.is_set():
            return StepCancelled()
        connection.request(
            "POST",
            "/v1/chat/completions",
            raw,
            {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
        )
        response = connection.getresponse()
        if response.status != 200:
            return StepUnavailable("model_http_error")
        if "text/event-stream" not in response.getheader("Content-Type", ""):
            return StepMalformed("stream_content_type", hashlib.sha256(b"").hexdigest())
        state = _State()
        total = 0
        done = False
        for _ in range(self.limits.max_chunks):
            if stopped.is_set():
                return StepCancelled()
            line = response.readline(self.limits.max_bytes + 1)
            total += len(line)
            if total > self.limits.max_bytes:
                return self._malformed("output_too_large", state)
            if not line:
                break
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                done = True
                break
            try:
                self._delta(object_value(strict_json(payload, max_bytes=self.limits.max_bytes)), state)
            except ModelRefused, KeyError, TypeError, ValueError:
                return self._malformed("malformed_stream", state)
            if state.first:
                first.set()
            update = StepProgress(
                state.first, self.tokens.count(state.content), self.tokens.count(state.thinking)
            )
            try:
                updates.put_nowait(update)
            except queue.Full:
                try:
                    updates.get_nowait()
                    updates.put_nowait(update)
                except queue.Empty, queue.Full:
                    pass
        if not done or not state.finish:
            return self._malformed("truncated_stream", state)
        return self._finish(state, request)

    def _delta(self, frame: dict[str, object], state: _State) -> None:
        usage = frame.get("usage")
        if isinstance(usage, dict):
            n = usage.get("prompt_tokens", 0)
            if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                raise ValueError("usage")
            state.input_tokens = n
        choices = frame.get("choices")
        if not isinstance(choices, list):
            raise ValueError("choices")
        for item in choices:
            choice = object_value(item)
            if choice.get("index", 0) != 0:
                raise ValueError("multiple choices")
            delta = object_value(choice.get("delta", {}))
            for key in ("content", "reasoning_content"):
                value = delta.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("delta")
                    if key == "content":
                        state.content += value
                    else:
                        state.thinking += value
                    if value:
                        state.first = True
            calls = delta.get("tool_calls", [])
            if not isinstance(calls, list):
                raise ValueError("calls")
            for value in calls:
                call = object_value(value)
                index = call.get("index")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or not 0 <= index < self.limits.max_tools
                ):
                    raise ValueError("call_index")
                current = state.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if "id" in call:
                    if not isinstance(call["id"], str):
                        raise ValueError("call_id")
                    current["id"] += call["id"]
                function = object_value(call.get("function", {}))
                for key in ("name", "arguments"):
                    if key in function:
                        if not isinstance(function[key], str):
                            raise ValueError("call_function")
                        current[key] += cast(str, function[key])
                state.first = True
            finish = choice.get("finish_reason")
            if finish is not None:
                if not isinstance(finish, str):
                    raise ValueError("finish")
                state.finish = finish

    def _finish(self, state: _State, request: StepRequest) -> StepResult:
        if "<think>" in state.content or "</think>" in state.content:
            return self._malformed("reasoning_not_separated", state)
        if state.finish not in ("stop", "tool_calls"):
            return self._malformed("generation_incomplete", state)
        tools = {str(t["name"]): t for t in request.bundle.tools or ()}
        parsed: list[ToolCall] = []
        ids: set[str] = set()
        try:
            for index in sorted(state.calls):
                c = state.calls[index]
                if not c["id"] or c["id"] in ids or c["name"] not in tools:
                    raise ModelRefused("call_not_exposed")
                args = object_value(strict_json(c["arguments"], max_bytes=self.limits.max_bytes))
                jsonschema.Draft202012Validator(object_value(tools[c["name"]]["input_schema"])).validate(args)
                ids.add(c["id"])
                parsed.append(ToolCall(c["id"], c["name"], cast(Mapping[str, JsonValue], args)))
            final = None
            if state.content:
                final = object_value(strict_json(state.content, max_bytes=self.limits.max_bytes))
                output_schema = request.bundle.output_validation_schema or request.bundle.response_schema
                if output_schema is not None:
                    jsonschema.Draft202012Validator(output_schema).validate(final)
            if not parsed and final is None:
                raise ModelRefused("empty_output")
            if parsed and final is not None:
                raise ModelRefused("mixed_output")
        except ModelRefused, jsonschema.ValidationError, jsonschema.SchemaError:
            return self._malformed("malformed_model_output", state)
        return StepOk(
            cast(Mapping[str, JsonValue] | None, final),
            tuple(parsed),
            state.finish,
            Usage(state.input_tokens, self.tokens.count(state.content), self.tokens.count(state.thinking)),
            request.bundle.prompt_sha256,
        )

    @staticmethod
    def _malformed(code: str, state: _State) -> StepMalformed:
        return StepMalformed(code, hashlib.sha256(state.content.encode("utf-8")).hexdigest())
