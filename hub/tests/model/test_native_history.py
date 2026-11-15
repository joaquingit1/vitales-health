"""Actual HTTP protocol oracle for E13's native multi-step exchange, not model quality evidence."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crew_health.assistant.application.content import Policy
from crew_health.assistant.application.controller import Candidate, Completed, Controller, Plan
from crew_health.assistant.application.json_data import JsonBounds
from crew_health.assistant.application.tools_copies import (
    ContextBudget,
    ContextSnapshot,
    Phase,
    ToolResult,
    ToolScope,
)
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.model.adapters.llama_client import ClientLimits, LlamaClient
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.prompt_assets import FilePromptAssets
from crew_health.model.application.ports import (
    DecodingSettings,
    JsonValue,
    PromptBundle,
    ToolCall,
    TranscriptItem,
)
from crew_health.model.application.prompt_builder import PromptBuilder, request_bytes
from crew_health.model.domain.release import ModelRefused


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def process_epoch(self) -> str:
        return "epoch_native_protocol"


class Counter:
    def count(self, text: str) -> int:
        return 1

    def count_request(self, body: Mapping[str, object]) -> int:
        return 1


class Cancel:
    def is_cancelled(self) -> bool:
        return False


class Boundary:
    def context(self, scope: ToolScope, budget: ContextBudget) -> ContextSnapshot:
        return ContextSnapshot(
            b"[]",
            (),
            (),
            scope.config_release_id,
            scope.evidence_release_id,
            scope.model_release_id,
            1,
            "same_material",
        )

    def fingerprint(self, context: ContextSnapshot) -> str:
        return context.snapshot_fingerprint

    def tool(self, scope: ToolScope, call: ToolCall) -> ToolResult:
        return ToolResult(
            call.id, "no_result", None, (), None, datetime.now(UTC), ("no_supported_passage",), None
        )

    def terminal(
        self, scope: ToolScope, call: ToolCall, result: ToolResult, seen: frozenset[str]
    ) -> Candidate | None:
        return None

    def final(self, scope: ToolScope, output: Mapping[str, JsonValue], seen: frozenset[str]) -> Candidate:
        return Candidate("text", output)

    def incomplete(self, scope: ToolScope, seen: frozenset[str]) -> Candidate:
        return Candidate("none", {})


@contextmanager
def protocol_server() -> Iterator[tuple[int, list[dict[str, object]], list[str]]]:
    bodies: list[dict[str, object]] = []
    denials: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            assert self.headers.get("Authorization") == "Bearer protocol_fixture_key"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            if len(bodies) > 1:
                messages = body["messages"]
                tools = [i for i, m in enumerate(messages) if m["role"] == "tool"]
                good = False
                if len(tools) == 1 and tools[0] > 0:
                    previous = messages[tools[0] - 1]
                    calls = previous.get("tool_calls", [])
                    good = (
                        previous["role"] == "assistant"
                        and len(calls) == 1
                        and calls[0].get("id") == "call_lookup"
                        and calls[0].get("function", {}).get("name") == "search_evidence"
                    )
                    good = (
                        good
                        and messages[tools[0]].get("tool_call_id") == "call_lookup"
                        and json.loads(messages[tools[0]]["content"])["status"] == "no_result"
                    )
                if not good:
                    denials.append("orphan_tool_result")
                    raw = b'{"error":{"message":"orphan tool result"}}'
                    self.send_response(400)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
            if len(bodies) == 1:
                delta = {
                    "reasoning_content": "PRIVATE_THINKING_CANARY",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_lookup",
                            "type": "function",
                            "function": {
                                "name": "search_evidence",
                                "arguments": json.dumps({"query": "protocol fixture query", "filters": {}}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {
                    "content": json.dumps(
                        {
                            "kind": "text",
                            "text": "Protocol complete",
                            "evidence_refs": [],
                            "reason": "Protocol fixture",
                            "missing_codes": [],
                        }
                    )
                }
                finish = "stop"
            frames = [{"choices": [{"delta": delta}]}, {"choices": [{"delta": {}, "finish_reason": finish}]}]
            raw = (
                b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
                + b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.005), daemon=True)
    thread.start()
    try:
        yield server.server_port, bodies, denials
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.trace("T-E13-011", "T-E13-050", "R17", "V27")
def test_actual_native_http_loop_sends_original_assistant_calls_before_real_tool_results(
    store: ManifestStore,
) -> None:
    clock = Clock()
    actor = Actor("person", "person_protocol")
    session = SessionContext("session_protocol", "crew", actor, actor.id, frozenset())
    anchor = TimeInput(clock.now(), "clock_protocol", "segment_protocol", 1, "hub_corrected", "SIM")
    scope = ToolScope(
        session,
        actor.id,
        "mission_protocol",
        "SIM",
        None,
        None,
        None,
        "turn_protocol",
        "conversation_protocol",
        "config_protocol",
        "evidence_protocol",
        "model_candidate",
        Phase.GENERAL,
        1,
        1,
        ElapsedDeadline(anchor, timedelta(seconds=90)),
    )
    plan = Plan(
        scope,
        Policy("not clinically reviewed", frozenset({"info", "watch"}), 8, 1, 90, 20, 10, ()),
        ContextBudget(32768, 64, ("facts",)),
        (TranscriptItem("user", "Protocol fixture request"),),
        JsonBounds(262144, 16, 10000, 200, 4000),
    )
    counter = Counter()
    with protocol_server() as (port, bodies, denials):
        controller = Controller(
            clock=clock,
            releases=store,
            prompts=PromptBuilder(store, FilePromptAssets(store), counter),
            inference=LlamaClient(
                port=port,
                api_key="protocol_fixture_key",
                tokens=counter,
                limits=ClientLimits(1048576, 64, 8, 1, 0.01),
            ),
            boundary=Boundary(),
        )
        outcome = controller.run(plan, cancel=Cancel(), progress=lambda p: None)
    assert isinstance(outcome, Completed), (outcome, denials)
    assert outcome.steps_used == 2 and len(bodies) == 2 and denials == []
    assert "PRIVATE_THINKING_CANARY" not in json.dumps(bodies)
    assert outcome.candidate.payload["text"] == "Protocol complete"


@pytest.mark.trace("T-E17-051", "R17")
def test_legacy_message_payload_is_byte_identical_without_native_history() -> None:
    bundle = PromptBundle(
        (TranscriptItem("user", "protocol fixture"),),
        None,
        "schema",
        DecodingSettings(0.2, 0.95, 20, 0.0, 17, 64, "deepseek"),
        "",
    )
    expected = {
        "messages": [{"role": "user", "content": "protocol fixture"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.2,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "seed": 17,
        "max_tokens": 64,
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_format": "deepseek",
        "model": "model_fixture",
    }
    assert request_bytes(bundle, "model_fixture") == canonical_json(expected)


CALL = ToolCall("call_lookup", "search_evidence", {"query": "protocol fixture query", "filters": {}})


def bundle_with(messages: tuple[TranscriptItem, ...]) -> PromptBundle:
    return PromptBundle(
        messages, None, "schema", DecodingSettings(0.2, 0.95, 20, 0.0, 17, 64, "deepseek"), ""
    )


@pytest.mark.trace("T-E13-011", "R17", "V27")
@pytest.mark.parametrize(
    "messages",
    [
        (TranscriptItem("tool", "{}", "orphan"),),
        (TranscriptItem("assistant", "", tool_calls=(CALL,)),),
        (
            TranscriptItem("assistant", "", tool_calls=(CALL,)),
            TranscriptItem("user", "interrupted"),
            TranscriptItem("tool", "{}", CALL.id),
        ),
        (TranscriptItem("assistant", "", tool_calls=(CALL,)), TranscriptItem("tool", "{}", "wrong")),
        (
            TranscriptItem("assistant", "", tool_calls=(CALL,)),
            TranscriptItem("tool", "{}", CALL.id),
            TranscriptItem("tool", "{}", CALL.id),
        ),
        (TranscriptItem("assistant", "", tool_calls=(CALL, CALL)), TranscriptItem("tool", "{}", CALL.id)),
        (
            TranscriptItem("assistant", "private reasoning", tool_calls=(CALL,)),
            TranscriptItem("tool", "{}", CALL.id),
        ),
        (TranscriptItem("user", "", tool_calls=(CALL,)), TranscriptItem("tool", "{}", CALL.id)),
        (TranscriptItem("user", "text", "misplaced"),),
        (
            TranscriptItem("assistant", "", tool_calls=(ToolCall("", "search_evidence", {}),)),
            TranscriptItem("tool", "{}", ""),
        ),
        (
            TranscriptItem("assistant", "", tool_calls=(CALL,)),
            TranscriptItem("tool", "{}", CALL.id),
            TranscriptItem("assistant", "", tool_calls=(CALL,)),
            TranscriptItem("tool", "{}", CALL.id),
        ),
    ],
)
def test_native_history_rejects_orphans_duplicates_missing_results_and_untrusted_prose(
    messages: tuple[TranscriptItem, ...],
) -> None:
    with pytest.raises(ModelRefused, match="tool_history_invalid"):
        request_bytes(bundle_with(messages), "model_fixture")


class Context:
    sections: Mapping[str, JsonValue] = {}


@pytest.mark.trace("T-E13-011", "R17", "V27")
@pytest.mark.parametrize(
    "call",
    [
        ToolCall("call_lookup", "unregistered_tool", {}),
        ToolCall("call_lookup", "search_evidence", {"query": "query", "filters": {}, "extra": True}),
        ToolCall("call_lookup", "search_evidence", {"query": None, "filters": {}}),
    ],
)
def test_pinned_catalog_checks_historical_call_schema(store: ManifestStore, call: ToolCall) -> None:
    transcript = (TranscriptItem("assistant", "", tool_calls=(call,)), TranscriptItem("tool", "{}", call.id))
    with pytest.raises(ModelRefused, match="tool_history_invalid"):
        PromptBuilder(store, FilePromptAssets(store), Counter()).build(
            "model_candidate", "general", Context(), transcript
        )


@pytest.mark.trace("T-E13-011", "R17", "V27")
def test_history_tool_limit_is_pinned_and_phase_changes_keep_valid_historical_calls(
    store: ManifestStore,
) -> None:
    transcript = (TranscriptItem("assistant", "", tool_calls=(CALL,)), TranscriptItem("tool", "{}", CALL.id))
    builder = PromptBuilder(store, FilePromptAssets(store), Counter())
    assert builder.build("model_candidate", "verifier", Context(), transcript).messages[-2].tool_calls == (
        CALL,
    )
    document = store.read_json(store.root / "releases/model_candidate.json")
    controller = document["controller"]
    assert isinstance(controller, dict)
    controller["max_tool_calls"] = 1
    from crew_health.model.adapters.manifest_store import durable_json

    durable_json(store.root / "releases/model_candidate.json", document)
    second = ToolCall("call_second", CALL.name, CALL.arguments)
    too_many = (
        *transcript,
        TranscriptItem("assistant", "", tool_calls=(second,)),
        TranscriptItem("tool", "{}", second.id),
    )
    with pytest.raises(ModelRefused, match="tool_history_invalid"):
        builder.build("model_candidate", "general", Context(), too_many)
