from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.assistant.domain.errors import AssistantRefused
from crew_health.assistant.domain.verification import (
    Claim,
    EvidenceRef,
    Support,
    VerifiedClaims,
    verify_claims,
)

OBS = EvidenceRef("observation", "ob_own")
PASSAGE = EvidenceRef("passage", "ps_approved")
ANSWER = EvidenceRef("answer", "an_own")


def run(claims: tuple[Claim, ...], support: tuple[Support, ...]) -> VerifiedClaims:
    return verify_claims(
        claims,
        support,
        subject_person_id="p_own",
        body_regions=frozenset({"chest"}),
        condition_ids=frozenset({"c1"}),
    )


@pytest.mark.trace("T-E13-040", "T-E13-041", "T-E13-046", "V23", "R16")
def test_failed_unseen_and_finding_only_support_cannot_support_hypothesis() -> None:
    own = Support(OBS, "p_own", True, True, True, finding_only=True)
    draft = (
        Claim("finding", "configured finding", (OBS,)),
        Claim("hypothesis", "configured hypothesis", (OBS, PASSAGE)),
    )
    result = run(draft, (own, Support(PASSAGE, None, False, True, True)))
    assert result.claims == (draft[0],)
    assert result.dropped_refs == 2
    assert result.withheld_claims == 1
    assert run((draft[0],), (replace(own, seen=False),)).claims == ()


@pytest.mark.trace("T-E13-043", "V25")
def test_foreign_reference_rejects_whole_output_even_when_unseen_or_unusable() -> None:
    with pytest.raises(AssistantRefused, match="foreign_evidence"):
        run((Claim("hypothesis", "clinical claim", (OBS,)),), (Support(OBS, "p_other", True, False, False),))


@pytest.mark.trace("T-E13-042", "T-E13-044", "INV-E13-04")
def test_passage_supports_hypothesis_but_not_finding_and_contradiction_requires_two_distinct_refs() -> None:
    support = (Support(OBS, "p_own", True, True, True), Support(PASSAGE, None, True, True, True))
    claims = (
        Claim("finding", "f", (PASSAGE,)),
        Claim("hypothesis", "h", (PASSAGE,)),
        Claim("contradiction", "c", (OBS, OBS)),
        Claim("contradiction", "d", (OBS, PASSAGE)),
    )
    result = run(claims, support)
    assert result.claims == (claims[1], claims[3])
    assert result.withheld_claims == 2


@pytest.mark.trace("T-E13-042", "INV-E13-03")
@pytest.mark.parametrize(
    "support",
    [
        Support(ANSWER, "p_own", True, True, True, presence="unknown"),
        Support(ANSWER, "p_own", True, True, True, presence="declined"),
        Support(ANSWER, "p_own", True, True, True, presence="answered", replay_exposure=True),
        Support(ANSWER, "p_own", True, True, True, presence="answered", habitat=True),
        Support(ANSWER, "p_own", False, True, True, presence="answered"),
        Support(ANSWER, "p_own", True, True, False, presence="answered"),
    ],
)
def test_each_failed_support_label_withholds_claim(support: Support) -> None:
    assert run((Claim("finding", "f", (ANSWER,)),), (support,)).claims == ()


@pytest.mark.trace("T-E13-042", "INV-E13-03", "INV-E13-04")
@given(usable=st.lists(st.booleans(), min_size=1, max_size=20))
def test_only_seen_usable_owned_refs_survive_and_duplicates_never_inflate_support(usable: list[bool]) -> None:
    refs = tuple(EvidenceRef("observation", f"ob_{i}") for i in range(len(usable)))
    support = tuple(Support(ref, "p_own", True, True, ok) for ref, ok in zip(refs, usable, strict=True))
    result = run((Claim("hypothesis", "h", refs + refs),), support)
    expected = tuple(ref for ref, ok in zip(refs, usable, strict=True) if ok)
    assert result.claims == ((Claim("hypothesis", "h", expected),) if expected else ())


@pytest.mark.trace("VR9", "T-E13-040")
@pytest.mark.parametrize(
    "claim",
    [Claim("finding", "f", (OBS,), "unknown"), Claim("hypothesis", "h", (OBS,), condition_ids=("unknown",))],
)
def test_unknown_body_region_or_condition_rejects_output(claim: Claim) -> None:
    with pytest.raises(AssistantRefused, match="invalid_claim_identity"):
        run((claim,), (Support(OBS, "p_own", True, True, True),))


@pytest.mark.trace("T-E13-043", "V25", "INV-E13-03")
def test_passage_reference_cannot_relabel_a_foreign_person_record() -> None:
    with pytest.raises(AssistantRefused, match="foreign_evidence"):
        run((Claim("hypothesis", "h", (PASSAGE,)),), (Support(PASSAGE, "p_other", True, True, True),))
