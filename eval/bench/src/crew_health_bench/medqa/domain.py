"""Pure rules of the MedQA proxy run: items, preregistered sample, answer parsing and scoring.

Model validation of a general medical knowledge proxy; never clinical validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from random import Random

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.metrics import category_metrics

OUTCOMES = ("correct", "wrong", "unparseable", "timeout")


@dataclass(frozen=True, slots=True)
class McqItem:
    item_id: str
    question: str
    options: tuple[tuple[str, str], ...]
    answer: str


@dataclass(frozen=True, slots=True)
class Sample:
    seed: int
    n: int
    item_ids: tuple[str, ...]
    warmup_ids: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return sha256("\n".join(self.item_ids).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Score:
    n: int
    correct: int
    wrong: int
    unparseable: int
    timeout: int
    accuracy: float
    interval: tuple[float, float]


def item_id(line_number: int, raw_line: bytes) -> str:
    """Stable identity: 1-based line number plus a short hash of the exact source bytes."""
    if line_number < 1:
        raise BenchError("medqa_line_invalid")
    return f"L{line_number:05d}-{sha256(raw_line).hexdigest()[:12]}"


def build_item(identity: str, document: object, letters: tuple[str, ...]) -> McqItem:
    if not isinstance(document, dict):
        raise BenchError("medqa_item_invalid")
    question, options, answer = document.get("question"), document.get("options"), document.get("answer_idx")
    if not isinstance(question, str) or not question.strip() or not isinstance(options, dict):
        raise BenchError("medqa_item_invalid")
    if tuple(sorted(options)) != letters or answer not in letters:
        raise BenchError("medqa_item_invalid")
    pairs = tuple((letter, options[letter]) for letter in letters)
    if any(not isinstance(value, str) or not value.strip() for _, value in pairs):
        raise BenchError("medqa_item_invalid")
    return McqItem(identity, question, tuple((k, str(v)) for k, v in pairs), str(answer))


def preregister(item_ids: tuple[str, ...], *, seed: int, n: int, warmup_n: int, max_n: int) -> Sample:
    """Seeded permutation; the sample is its prefix and the warm-up its tail, so they never overlap."""
    unique = tuple(sorted(set(item_ids)))
    if len(unique) != len(item_ids) or not 1 <= n <= max_n or warmup_n < 0 or n + warmup_n > len(unique):
        raise BenchError("medqa_sample_invalid")
    order = list(unique)
    Random(seed).shuffle(order)
    warmup = tuple(order[len(order) - warmup_n :]) if warmup_n else ()
    return Sample(seed, n, tuple(order[:n]), warmup)


_ANSWER = re.compile(r"^(?:answer\s*(?:is)?\s*[:\-]?\s*)?[\(\[]?([A-Z])[\)\]]?(?:[.):,]|\s|$)", re.IGNORECASE)


def parse_answer(response: str | None, letters: tuple[str, ...]) -> str | None:
    """Preregistered rule: after trimming, the reply must start with one option letter (optionally
    after 'Answer:'), uppercase only, then punctuation, whitespace or end; anything else is unparseable."""
    if response is None:
        return None
    stripped = response.strip().lstrip("*").strip()
    match = _ANSWER.match(stripped)
    if match is None:
        return None
    letter = match.group(1)
    return letter if letter in letters and letter.isupper() else None


def outcome(response: str | None, *, timed_out: bool, answer: str, letters: tuple[str, ...]) -> str:
    if timed_out:
        return "timeout"
    letter = parse_answer(response, letters)
    if letter is None:
        return "unparseable"
    return "correct" if letter == answer else "wrong"


def score(outcomes: tuple[str, ...], *, n: int) -> Score:
    """Every preregistered item stays in the denominator: a missing row is a failure, not a skip."""
    if len(outcomes) > n or n < 1 or any(item not in OUTCOMES for item in outcomes):
        raise BenchError("medqa_outcomes_invalid")
    graded = tuple("pass" if item == "correct" else "fail" for item in outcomes)
    padded = graded + ("fail",) * (n - len(outcomes))
    metrics = category_metrics(padded, n_cases=n)
    if metrics.rate is None or metrics.interval is None:
        raise BenchError("medqa_outcomes_invalid")
    return Score(
        n,
        outcomes.count("correct"),
        outcomes.count("wrong"),
        outcomes.count("unparseable"),
        outcomes.count("timeout") + (n - len(outcomes)),
        metrics.rate,
        metrics.interval,
    )


def render_prompt(item: McqItem, instruction: str) -> str:
    lines = [item.question.strip(), ""]
    lines.extend(f"{letter}. {text.strip()}" for letter, text in item.options)
    lines.extend(("", instruction))
    return "\n".join(lines)
