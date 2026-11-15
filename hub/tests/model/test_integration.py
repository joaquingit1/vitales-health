from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.model.adapters.activation import ActivationService
from crew_health.model.adapters.launch import ALLOWED, LaunchBuilder, validate_flags
from crew_health.model.adapters.manifest_store import ManifestStore, durable_json
from crew_health.model.adapters.prompt_assets import FilePromptAssets
from crew_health.model.adapters.stub import StubResponder
from crew_health.model.application.ports import Deadline, JsonValue, StepOk, StepUnavailable
from crew_health.model.application.promotion import promote
from crew_health.model.application.prompt_builder import PromptBuilder, request_body
from crew_health.model.domain.release import ModelRefused

from .test_transport import Cancel, TestClock, Tokens, request


@dataclass
class Context:
    sections: dict[str, JsonValue]


@pytest.mark.trace("R22", "T-E17-002", "T-E17-044", "T-E17-070", "T-E17-072")
def test_real_release_config_changes_prompt_and_identity(store: ManifestStore) -> None:
    builder = PromptBuilder(store, FilePromptAssets(store), Tokens())
    context = Context({"facts": {"origin": "SIM", "value": 0}, "evidence": []})
    one = builder.build("model_candidate", "general", context, ())
    two = builder.build(
        "model_candidate", "general", Context({"evidence": [], "facts": {"value": 0, "origin": "SIM"}}), ()
    )
    assert one == two
    assert "untrusted_context" in one.messages[1].content
    document = store.read_json(store.root / "releases/model_candidate.json")
    decoding = document["decoding"]
    assert isinstance(decoding, dict)
    decoding["temperature"] = 0.4
    maxima = decoding["max_tokens_by_phase"]
    assert isinstance(maxima, dict)
    maxima["general"] = 128
    durable_json(store.root / "releases/model_candidate.json", document)
    changed = builder.build("model_candidate", "general", context, ())
    assert request_body(changed)["temperature"] == 0.4
    assert request_body(changed)["max_tokens"] == 128
    assert changed.prompt_sha256 != one.prompt_sha256
    assert store.load("model_candidate").document_json == store.get("model_candidate").document_json


@pytest.mark.trace("R22", "T-E17-044")
@given(
    values=st.dictionaries(
        st.text(alphabet="abc", min_size=1, max_size=3), st.integers(-100, 100), max_size=5
    )
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_section_order_does_not_change_prompt_hash(values: dict[str, int], store: ManifestStore) -> None:
    builder = PromptBuilder(store, FilePromptAssets(store), Tokens())
    original = builder.build("model_candidate", "general", Context(dict(values)), ())
    reverse = builder.build("model_candidate", "general", Context(dict(reversed(list(values.items())))), ())
    assert original.prompt_sha256 == reverse.prompt_sha256


@pytest.mark.trace("R22", "T-E17-001")
def test_corrupt_template_and_folder_manifest_fail_closed(store: ManifestStore) -> None:
    template = next((store.root / "templates").glob("*.jinja"))
    template.write_text("changed", encoding="utf-8")
    with pytest.raises(ModelRefused, match="template_hash_mismatch"):
        store.get("model_candidate")


@pytest.mark.trace("R23", "T-E17-032", "T-E17-036", "T-E17-037")
@given(
    st.sampled_from(
        [
            "--lora",
            "--lora-scaled",
            "--control-vector",
            "--override-kv",
            "-hf",
            "--model-url",
            "--unknown",
            "--host=0.0.0.0",
        ]
    )
)
def test_launch_rejects_unsupported_flag_even_when_help_advertises_it(flag: str) -> None:
    with pytest.raises(ModelRefused):
        validate_flags(("llama-server", flag), " ".join(ALLOWED) + " " + flag)


def live(store: ManifestStore, manifest: dict[str, Any]) -> Path:
    document = copy.deepcopy(manifest)
    document["mode"] = "live"
    payload = b"EXPLICIT_FAKE_GGUF_FOR_TRANSPORT_TESTS"
    digest = hashlib.sha256(payload).hexdigest()
    document["weights"] = {
        "file_name": digest + ".gguf",
        "weights_sha256": digest,
        "size_bytes": len(payload),
        "quantization": "Q4_K_M",
        "lineage": {
            "source_files_sha256": "a" * 64,
            "bf16_gguf_sha256": "b" * 64,
            "equivalence_report_sha256": "c" * 64,
            "reproducibility_report_sha256": "d" * 64,
            "llama_cpp_commit": "620d2225cc436ab0b001e3200e590e3b286ef9ab",
        },
    }
    document["basis"]["weights.size_bytes"] = "Measured explicit fake fixture bytes, not model weights"
    durable_json(store.root / "releases/model_candidate.json", document)
    path = store.data_dir / "models" / (digest + ".gguf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o444)
    return path


@pytest.mark.trace("R23", "T-E17-032", "T-E17-035", "T-E17-036", "T-E17-037", "T-E17-071")
def test_launch_uses_only_manifest_readonly_weights_and_known_loopback_flags(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    path = live(store, manifest)
    help_text = (store.root / "build/llama-server-help.txt").read_text(encoding="utf-8")
    binary = store.root / "build/pinned-test-llama-server"
    binary.write_bytes(b"EXPLICIT_FAKE_RUNTIME_BINARY")
    pin = store.read_json(store.root / "pins/llama-cpp.json")
    pin["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    durable_json(store.root / "pins/llama-cpp.json", pin)
    builder = LaunchBuilder(
        store,
        binary=binary,
        port=18081,
        captured_help=help_text,
        memory_budget_bytes=7500000000,
        cpu_budget=6,
        nice=10,
    )
    spec = builder.launch_spec("model_candidate")
    assert spec.argv[spec.argv.index("--host") + 1] == "127.0.0.1"
    assert "--offline" in spec.argv and "--log-disable" in spec.argv
    document = store.read_json(store.root / "releases/model_candidate.json")
    runtime = document["runtime"]
    assert isinstance(runtime, dict)
    runtime["context_tokens"] = 16384
    durable_json(store.root / "releases/model_candidate.json", document)
    changed = builder.launch_spec("model_candidate")
    assert changed.argv[changed.argv.index("--ctx-size") + 1] == "16384"
    path.chmod(0o644)
    with pytest.raises(ModelRefused, match="weights_writable"):
        builder.launch_spec("model_candidate")


@pytest.mark.trace("E17-03", "T-E17-052", "T-E17-043")
def test_activate_retry_restart_and_rollback_preserve_previous_pointer(store: ManifestStore) -> None:
    service = ActivationService(store, now=lambda: datetime(2026, 11, 15, tzinfo=UTC))
    one = service.activate("model_candidate", actor_id="operator_test")
    assert service.activate("model_candidate", actor_id="operator_test") == one
    second = store.read_json(store.root / "releases/model_candidate.json")
    second["release_id"] = "model_second"
    durable_json(store.root / "releases/model_second.json", second)
    service.activate("model_second", actor_id="operator_test")
    restarted = ActivationService(
        ManifestStore(store.root, store.data_dir, profile="eval"),
        now=lambda: datetime(2026, 11, 15, tzinfo=UTC),
    )
    pointer = restarted.rollback(actor_id="operator_test")
    assert pointer["release_id"] == "model_candidate" and pointer["previous_release_id"] == "model_second"
    assert store.get("model_second").release_id == "model_second"
    flight = ManifestStore(store.root, store.data_dir, profile="flight")
    with pytest.raises(ModelRefused, match="stub_in_flight"):
        flight.active()


@pytest.mark.trace("R22", "R23", "T-E17-034", "T-E17-050", "T-E17-051")
def test_promotion_cannot_treat_protocol_tests_as_measured_probes(
    store: ManifestStore, manifest: dict[str, Any], gate: dict[str, object], policy: dict[str, object]
) -> None:
    live(store, manifest)

    class Evidence:
        def reports(self, release_id: str) -> tuple[dict[str, object], ...]:
            return tuple({"probe": f"P{n}", "verdict": "pass", "measured": False} for n in range(1, 9))

        def lineage(self, release_id: str) -> bool:
            return True

    with pytest.raises(ModelRefused, match="probes_missing"):
        promote(store.read_json(store.root / "releases/model_candidate.json"), gate, policy, Evidence())


@pytest.mark.trace("D25", "D17", "T-E17-041", "T-E17-043")
def test_stub_only_replays_exact_step_in_explicit_profile() -> None:
    script = {
        "scenario": "demo",
        "entries": [
            {
                "conversation_ordinal": 0,
                "phase": "general",
                "step_index": 0,
                "final": {"kind": "stop"},
                "tool_calls": [],
                "thinking_canary": "NEVER_PERSIST_THIS",
            }
        ],
    }
    responder = StubResponder(script, profile="demo", scenario="demo", conversation_ordinal=0)
    clock = TestClock()
    now = clock.monotonic_ns()
    deadline = Deadline(now + int(1e9), now + int(1e9), clock)
    result = responder.run_step(request(), deadline=deadline, cancel=Cancel(), progress=lambda value: None)
    assert isinstance(result, StepOk) and result.final == {"kind": "stop"}
    assert "NEVER_PERSIST_THIS" not in repr(result)
    assert isinstance(
        responder.run_step(request(), deadline=deadline, cancel=Cancel(), progress=lambda value: None),
        StepUnavailable,
    )
    with pytest.raises(ModelRefused, match="stub_in_flight"):
        StubResponder(script, profile="flight", scenario="demo", conversation_ordinal=0)


@pytest.mark.trace("R22", "R23", "T-E17-050")
def test_direct_flight_activation_cannot_bypass_signed_audited_publication(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    live(store, manifest)
    document = store.read_json(store.root / "releases/model_candidate.json")
    document["status"] = "promoted"
    durable_json(store.root / "releases/model_candidate.json", document)
    flight = ManifestStore(store.root, store.data_dir, profile="flight")
    service = ActivationService(flight, now=lambda: datetime(2026, 11, 15, tzinfo=UTC))
    with pytest.raises(ModelRefused, match="signed_audited_activation_binding_unavailable"):
        service.activate("model_candidate", actor_id="operator_test")
    assert not (store.data_dir / "models/active.json").exists()


@pytest.mark.trace("R22", "T-E17-044")
def test_runtime_pin_detects_changed_versioned_prompt_manifest(store: ManifestStore) -> None:
    before = store.fingerprint("model_candidate")
    prompt = store.root / "prompts/v1/system.general.md"
    prompt.write_text("Changed prompt mechanism wording", encoding="utf-8")
    hashes = store.read_json(prompt.parent / "MANIFEST.sha256")
    hashes[prompt.name] = hashlib.sha256(prompt.read_bytes()).hexdigest()
    durable_json(prompt.parent / "MANIFEST.sha256", hashes)
    assert store.fingerprint("model_candidate") != before


@pytest.mark.trace("R23", "T-E17-032", "T-E17-033")
def test_launch_refuses_binary_not_in_pinned_runtime_build(
    store: ManifestStore, manifest: dict[str, Any]
) -> None:
    live(store, manifest)
    binary = store.root / "build/untrusted-llama-server"
    binary.write_bytes(b"UNTRUSTED_BINARY")
    builder = LaunchBuilder(
        store,
        binary=binary,
        port=18081,
        memory_budget_bytes=7500000000,
        cpu_budget=6,
        nice=10,
        captured_help=(store.root / "build/llama-server-help.txt").read_text(encoding="utf-8"),
    )
    with pytest.raises(ModelRefused, match="runtime_binary_mismatch"):
        builder.launch_spec("model_candidate")


@pytest.mark.trace("R17", "T-E17-073")
def test_real_catalog_exposes_acquisition_in_intake_and_help_and_assessment_draft(
    store: ManifestStore,
) -> None:
    catalog = store.tool_catalog("model_candidate")
    assert "propose_measurement" in catalog.exposure["intake"]
    assert "propose_measurement" in catalog.exposure["help"]
    assert "draft_assessment" in catalog.exposure["assessment"]
    assert not catalog.exposure["verifier"]
