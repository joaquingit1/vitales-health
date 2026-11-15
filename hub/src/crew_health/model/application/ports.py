"""E17 4.2 public transport types. No dependency on a feature domain."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from crew_health.kernel.clock import Clock

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
Phase = Literal["intake", "assessment", "disposition", "help", "insight", "general", "verifier"]
Profile = Literal["flight", "demo", "eval"]


@dataclass(frozen=True, slots=True)
class ModelRelease:
    document_json: bytes

    @property
    def document(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self.document_json))

    @property
    def release_id(self) -> str:
        return str(self.document["release_id"])

    @property
    def mode(self) -> str:
        return str(self.document["mode"])


@dataclass(frozen=True, slots=True)
class ModelReleaseRef(ModelRelease):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingPin:
    model_id: str
    revision: str
    sha256: str
    dims: int


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: tuple[Mapping[str, JsonValue], ...]
    exposure: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    values: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ContextSettings:
    values: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    values: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ControllerSettings:
    values: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class DecodingSettings:
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    seed: int
    max_tokens: int
    reasoning_format: str


class ContextSnapshot(Protocol):
    """Structural projection of E12's bounded, authorized sections."""

    @property
    def sections(self) -> Mapping[str, JsonValue]: ...


@dataclass(frozen=True, slots=True)
class TranscriptItem:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromptBundle:
    messages: tuple[TranscriptItem, ...]
    tools: tuple[Mapping[str, JsonValue], ...] | None
    output_schema_id: str
    decoding: DecodingSettings
    prompt_sha256: str
    response_schema: Mapping[str, JsonValue] | None = None
    output_validation_schema: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class StepRequest:
    release_id: str
    phase: Phase
    bundle: PromptBundle


@dataclass(frozen=True, slots=True)
class Deadline:
    total_ns: int
    first_token_ns: int
    clock: Clock


class CancelToken(Protocol):
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class StepProgress:
    first_token_seen: bool
    output_tokens: int
    thinking_tokens: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int


@dataclass(frozen=True, slots=True)
class StepOk:
    final: Mapping[str, JsonValue] | None
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    prompt_sha256: str


@dataclass(frozen=True, slots=True)
class StepMalformed:
    error_code: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class StepTimedOut:
    kind: Literal["first_token", "total"]


@dataclass(frozen=True, slots=True)
class StepCancelled:
    pass


@dataclass(frozen=True, slots=True)
class StepUnavailable:
    code: str


StepResult = StepOk | StepMalformed | StepTimedOut | StepCancelled | StepUnavailable


@dataclass(frozen=True, slots=True)
class RuntimeProbe:
    healthy: bool
    identity_ok: bool
    adapters_empty: bool
    detail_code: str


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    process_id: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str
    health_url: str
    ready_timeout_s: float
    memory_budget_bytes: int
    cpu_budget: int
    nice: int


class ModelReleaseReader(Protocol):
    def active(self) -> ModelRelease | None: ...
    def get(self, release_id: str) -> ModelRelease: ...
    def load(self, release_id: str) -> ModelReleaseRef: ...
    def tool_catalog(self, model_release_id: str) -> ToolCatalog: ...
    def retrieval_settings(self, model_release_id: str) -> RetrievalSettings: ...
    def context_settings(self, model_release_id: str) -> ContextSettings: ...
    def runtime_settings(self, model_release_id: str) -> RuntimeSettings: ...
    def controller_settings(self, model_release_id: str) -> ControllerSettings: ...
    def embedding_pin(self) -> EmbeddingPin: ...


class PromptPort(Protocol):
    def build(
        self, release_id: str, phase: Phase, context: ContextSnapshot, transcript: Sequence[TranscriptItem]
    ) -> PromptBundle: ...


class InferencePort(Protocol):
    def run_step(
        self,
        request: StepRequest,
        *,
        deadline: Deadline,
        cancel: CancelToken,
        progress: Callable[[StepProgress], None],
    ) -> StepResult: ...


class ModelRuntimePort(Protocol):
    def start(self, release_id: str) -> None: ...
    def stop(self) -> None: ...
    def probe(self, *, timeout_s: float) -> RuntimeProbe: ...


class ModelLaunchSpecPort(Protocol):
    def launch_spec(self, release_id: str) -> LaunchSpec: ...


class SupervisorControl(Protocol):
    def enable(self, process_id: str, spec: LaunchSpec) -> None: ...
    def disable(self, process_id: str) -> None: ...
    def state(self, process_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ModelInstallation:
    """Deployment-owned binding, absent when this node has no installed model artifacts."""

    artifact_root: str
    data_dir: str
    profile: Profile
    binary: str
    port: int


class ModelDeployment(Protocol):
    @property
    def installation(self) -> ModelInstallation: ...


class ModelArtifactTrust(Protocol):
    """E20: provisioned signing key and exact model bundle/member verification."""

    def verify(self, release: ModelRelease) -> None: ...


class ModelActivationPublisher(Protocol):
    """E01/E20: durable audited node publication with expected-head fencing, outside inference."""

    def publish(
        self,
        pointer: Mapping[str, JsonValue],
        *,
        expected_release_id: str | None,
        actor_id: str,
        model_release_sha256: str,
    ) -> None: ...


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def count_request(self, body: Mapping[str, object]) -> int: ...


@dataclass(frozen=True, slots=True)
class StubInstallation:
    """E21's explicitly selected, release-hash-pinned scenario and tokenizer binding."""

    script_json: bytes
    scenario: str
    conversation_ordinal: int
    tokens: TokenCounter


class ModelPromotionContext(Protocol):
    """E13 supplies the currently active assistant policy, never a caller-supplied default."""

    def turn_timeout_seconds(self) -> int: ...
