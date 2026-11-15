"""M2: HealthBench fingerprints are one row per text (prompt message, rubric line, ideal completion), so one
copied rubric line is caught even next to a long prompt and completion, and the sample keeps a floor of
shingles per text. Synthetic text only (design section 12)."""

from __future__ import annotations

import random

import pytest

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.leak import Fingerprint, part_fingerprints, scan_text

pytestmark = [pytest.mark.e18]


def _text(rng: random.Random, words: int) -> str:
    return " ".join("w" + str(rng.randrange(5000)) for _ in range(words))


@pytest.mark.trace("R23", "V33", "T-E18-03", "INV-E18-02")
def test_one_copied_rubric_line_is_flagged_beside_a_long_prompt_and_completion() -> None:
    rng = random.Random(7)
    rubric = [_text(rng, 22) for _ in range(12)]
    parts = (_text(rng, 250), *rubric, _text(rng, 350))
    kept = part_fingerprints(parts, fp_salt="public", sample_modulus=16, min_kept=3)
    assert len(kept) == len(parts) and all(len(item) >= 3 for item in kept)
    rows = tuple(Fingerprint("healthbench:x:" + str(index), item, ()) for index, item in enumerate(kept))
    findings = scan_text(rubric[0], rows, fp_salt="public", leak_jaccard=0.2, target="prompts")
    assert {finding.case_ref for finding in findings} == {"healthbench:x:1"}
    assert not scan_text(_text(rng, 40), rows, fp_salt="public", leak_jaccard=0.2, target="prompts")


@pytest.mark.trace("T-E18-03")
def test_a_short_text_keeps_its_one_shingle_and_bounds_are_checked() -> None:
    (kept,) = part_fingerprints(("too short",), fp_salt="public", sample_modulus=16, min_kept=8)
    assert len(kept) == 1, "a text under eight words is one shingle, kept despite the sample"
    with pytest.raises(BenchError):
        part_fingerprints(("a b c",), fp_salt="public", sample_modulus=0, min_kept=8)
