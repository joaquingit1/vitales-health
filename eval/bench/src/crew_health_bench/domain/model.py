from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.units import Quantity


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class DecodingSettings:
    temperature: Decimal
    top_p: Decimal
    top_k: int
    min_p: Decimal
    seed: int
    max_tokens: int
    reasoning_format: str

    def request(self) -> dict[str, object]:
        return {
            "temperature": float(self.temperature),
            "top_p": float(self.top_p),
            "top_k": self.top_k,
            "min_p": float(self.min_p),
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "reasoning_format": self.reasoning_format,
        }


@dataclass(frozen=True)
class ModelReleaseRef:
    release_id: str
    model_id: str
    weights_sha256: str
    context_tokens: int
    decoding: DecodingSettings
    turn_timeout: Quantity
    projection: Mapping[str, object]


@dataclass(frozen=True)
class EndpointIdentity:
    model_path: str
    context_tokens: int


@dataclass(frozen=True)
class Completion:
    text: str
    thinking_tokens: int | None
    usage: Mapping[str, int]
    finish_reason: str


@dataclass(frozen=True)
class BenchmarkAnswer:
    text: str
    tool_calls: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    finish_reason: str
