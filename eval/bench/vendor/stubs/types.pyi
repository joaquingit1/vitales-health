from dataclasses import dataclass, field

MessageList = list[dict[str, str]]

@dataclass
class SamplerResponse:
    response_text: str
    actual_queried_message_list: MessageList
    response_metadata: dict[str, object]

class SamplerBase:
    def __call__(self, message_list: MessageList) -> SamplerResponse: ...

@dataclass
class EvalResult:
    score: float | None
    metrics: dict[str, float] | None
    htmls: list[str]
    convos: list[MessageList]
    metadata: dict[str, object] | None

@dataclass
class SingleEvalResult:
    score: float | None
    metrics: dict[str, float] = field(default_factory=dict)
    html: str | None = None
    convo: MessageList | None = None
    example_level_metadata: dict[str, object] | None = None
