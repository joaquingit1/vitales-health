from .types import EvalResult, MessageList, SamplerBase, SingleEvalResult

class RubricItem:
    criterion: str
    points: float
    tags: list[str]
    def __init__(self, criterion: str, points: float, tags: list[str]) -> None: ...
    def to_dict(self) -> dict[str, object]: ...
    @classmethod
    def from_dict(cls, d: dict[str, object]) -> RubricItem: ...

class HealthBenchEval:
    examples: list[dict[str, object]]
    def __init__(
        self,
        grader_model: SamplerBase,
        num_examples: int | None = None,
        n_repeats: int = 1,
        physician_completions_mode: str | None = None,
        run_reference_completions: bool = False,
        n_threads: int = 120,
        subset_name: str | None = None,
        input_path: str | None = None,
        length_adjustment_center: float | None = None,
        length_adjustment_penalty_per_500_chars: float | None = None,
    ) -> None: ...
    def grade_sample(
        self, prompt: MessageList, response_text: str, example_tags: list[str], rubric_items: list[RubricItem]
    ) -> tuple[dict[str, float], str, list[dict[str, object]]]: ...
    def __call__(self, sampler: SamplerBase) -> EvalResult: ...

def calculate_score(
    rubric_items: list[RubricItem], grading_response_list: list[dict[str, object]]
) -> float | None: ...
def _aggregate_get_clipped_mean(single_eval_results: list[SingleEvalResult]) -> EvalResult: ...
