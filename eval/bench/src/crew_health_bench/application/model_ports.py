from collections.abc import Sequence
from typing import Protocol

from crew_health.kernel.units import Quantity

from crew_health_bench.domain.model import BenchmarkAnswer, ChatMessage, Completion, DecodingSettings, EndpointIdentity


class ModelEndpoint(Protocol):
    def identity(self, *, timeout: Quantity) -> EndpointIdentity: ...
    def complete(
        self, messages: Sequence[ChatMessage], settings: DecodingSettings, *, timeout: Quantity
    ) -> Completion: ...


class BenchmarkAnswerPort(Protocol):
    def answer(self, messages: Sequence[ChatMessage], *, evidence_release_id: str,
               tool_profile: str, timeout: Quantity) -> BenchmarkAnswer: ...
