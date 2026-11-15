"""T-E18-03, 04, 09, 17: policy edits and contamination detection."""

from pathlib import Path

import pytest

from crew_health_bench.adapters.documents import load_document
from crew_health_bench.domain.claims import ClaimsPolicy, lint_claims
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.leak import Fingerprint, scan_text
from crew_health_bench.domain.text import fingerprints

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "R30", "V33")]
CONFIG = Path(__file__).parents[1] / "config"


def test_t_e18_09_claims_and_disclaimer() -> None:
    policy = ClaimsPolicy.from_document(load_document(CONFIG / "claims-policy.json", max_file_bytes=16777216))
    for text in ("HealthBench diagnostic accuracy 62%", "validated by HealthBench"):
        assert lint_claims(text, policy)
    assert not lint_claims(policy.benchmark_disclaimer, policy)
    changed = ClaimsPolicy(
        policy.benchmark_disclaimer,
        (*policy.forbidden_near_benchmark, "excellent"),
        policy.window_words,
        policy.publisher_scores,
    )
    assert not lint_claims("HealthBench excellent", policy)
    assert lint_claims("HealthBench excellent", changed)


@pytest.mark.v33
@pytest.mark.parametrize("kind", ["prompts", "tools", "examples", "config", "evidence", "dev"])
def test_t_e18_03_04_containment_in_large_targets(kind: str) -> None:
    paragraph = " ".join(f"holdout{i}" for i in range(60))
    fp = Fingerprint("salted_ref", fingerprints(paragraph, fp_salt="public"), ("hidden_canary",))
    surrounding = " ".join(f"other{i}" for i in range(3000))
    assert scan_text(
        surrounding + paragraph + " " + surrounding, (fp,), fp_salt="public", leak_jaccard=0.2, target=kind
    )
    assert scan_text("hidden_canary", (fp,), fp_salt="public", leak_jaccard=0.2, target=kind)
    assert not scan_text(surrounding, (fp,), fp_salt="public", leak_jaccard=0.2, target=kind)


def test_t_e18_17_documents_have_schema_and_no_missing_fallback(tmp_path: Path) -> None:
    for document in sorted(CONFIG.glob("*.json")):
        assert load_document(document, max_file_bytes=16777216)
    with pytest.raises(BenchError):
        load_document(tmp_path / "missing.json", max_file_bytes=16777216)


def test_t_e18_03_missing_fingerprints_refused() -> None:
    with pytest.raises(BenchError):
        Fingerprint("ref", frozenset(), ())
    with pytest.raises(BenchError):
        Fingerprint("ref", frozenset(), ("",))
    with pytest.raises(BenchError):
        scan_text("target", (), fp_salt="salt", leak_jaccard=0.2, target="config")
    fp = Fingerprint("ref", frozenset(), ("marker",))
    assert not scan_text("target", (fp,), fp_salt="salt", leak_jaccard=0.2, target="config")
    with pytest.raises(BenchError):
        scan_text("target", (fp,), fp_salt="salt", leak_jaccard=0, target="config")
