from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.documents import BenchSettings, decode_json, load_document
from crew_health_bench.adapters.model_endpoint import LlamaEndpoint
from crew_health_bench.adapters.model_release import load_release
from crew_health_bench.domain.documents import number, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.model import ChatMessage


class Arguments(argparse.Namespace):
    config_dir: Path
    model_release: Path
    schema: Path
    base_url: str


def execute(args: Arguments, data: bytes, settings: BenchSettings) -> dict[str, object]:
    row = decode_json(data)
    if set(row) != {"prompt", "timeout"} or not isinstance(row["prompt"], list):
        raise BenchError("case_invalid")
    messages: list[ChatMessage] = []
    for value in row["prompt"]:
        message = object_map(value)
        if set(message) != {"role", "content"}:
            raise BenchError("case_invalid")
        messages.append(ChatMessage(text(message["role"]), text(message["content"])))
    timeout = object_map(row["timeout"])
    limit = Quantity(number(timeout.get("value")), Unit(text(timeout.get("unit"))))
    release = load_release(
        args.model_release, args.schema, max_file_bytes=settings.integer("bench.store.max_line_bytes")
    )
    if limit.unit.code != "s" or not 0 < limit.value <= release.turn_timeout.value:
        raise BenchError("case_invalid")
    with httpx.Client(trust_env=False) as client:
        endpoint = LlamaEndpoint(args.base_url, settings, client)
        calls = 0
        try:
            endpoint.require_identity(release, timeout=settings.quantity("bench.model.connect_timeout"))
            calls = 1
            completion = endpoint.complete(messages, release.decoding, timeout=limit)
            endpoint.require_identity(release, timeout=settings.quantity("bench.model.connect_timeout"))
        except BenchError as error:
            if error.reason not in ("timeout", "model_response_invalid"):
                raise
            return {
                "status": "timeout" if error.reason == "timeout" else "failed_generation",
                "text": None,
                "model_calls": calls,
                "finish_reason": error.reason,
                "model_release": dict(release.projection),
            }
    return {
        "status": "completed",
        "text": completion.text,
        "model_calls": 1,
        "finish_reason": completion.finish_reason,
        "usage": dict(completion.usage),
        "thinking_tokens": completion.thinking_tokens,
        "model_release": dict(release.projection),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--model-release", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    args = Arguments()
    parser.parse_args(namespace=args)
    try:
        plan = load_document(args.config_dir / "healthbench-plan.json", max_file_bytes=1 << 16)
        key_name = text(object_map(plan["grader"])["api_key_env"])
        if "CHC_EVAL_IDENTITY" in os.environ or key_name in os.environ:
            raise BenchError("holdout_key_required", exit_code=5)
        settings = BenchSettings(
            load_document(args.config_dir / "bench-settings.json", max_file_bytes=1 << 16)
        )
        maximum = settings.integer("bench.store.max_line_bytes")
        data = sys.stdin.buffer.read(maximum + 1)
        if len(data) > maximum:
            raise BenchError("case_invalid")
        result = execute(args, data, settings)
        sys.stdout.write(json.dumps(result, allow_nan=False, separators=(",", ":")) + "\n")
        return 0
    except BenchError as error:
        sys.stderr.write(json.dumps({"reason": error.reason}) + "\n")
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
