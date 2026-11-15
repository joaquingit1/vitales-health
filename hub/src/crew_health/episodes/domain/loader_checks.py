"""EP6 validates the existing ui-copy release seam without inventing episode-rules as a ConfigName."""

from __future__ import annotations

import json

from crew_health.episodes.domain.types import REOPEN_REASONS, EpisodeState, ResolutionReason
from crew_health.kernel.config import CandidateRelease, ConfigName, Finding


class EpisodeWordsCheck:
    check_id = "episodes.EP6"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        if not self.enabled:
            return ()
        view = candidate.documents.get(ConfigName.UI_COPY)
        if view is None:
            return (Finding(self.check_id, ConfigName.UI_COPY, "/", "episode words unavailable"),)
        value: object = json.loads(view.raw)
        data = value if isinstance(value, dict) else {}
        episode = data.get("episode_words", {})
        words = episode if isinstance(episode, dict) else {}
        specs = (
            (
                "/review_role_words",
                data.get("review_role_words"),
                frozenset({"crew_medical_officer", "acting_medical_officer", "ground"}),
            ),
            ("/episode_words/state", words.get("state"), frozenset(s.value for s in EpisodeState)),
            (
                "/resolution_reason_words",
                data.get("resolution_reason_words"),
                frozenset(r.value for r in ResolutionReason),
            ),
            (
                "/reopen_reason_words",
                data.get("reopen_reason_words"),
                REOPEN_REASONS
                | frozenset(
                    {"answer_after_resolution", "urgency_raised", "new_activity", "review_unresolved"}
                ),
            ),
            ("/task_kind_words", data.get("task_kind_words"), frozenset({"review", "review_item", "custom"})),
            (
                "/review_decision_words",
                data.get("review_decision_words"),
                frozenset({"agree", "disagree", "needs_more", "not_assessed"}),
            ),
        )
        findings = []
        for pointer, mapping, required in specs:
            actual = mapping if isinstance(mapping, dict) else {}
            for key in sorted(required):
                label = actual.get(key)
                if not isinstance(label, str) or not label.strip():
                    findings.append(
                        Finding(
                            self.check_id, ConfigName.UI_COPY, pointer + "/" + key, "episode word missing"
                        )
                    )
        return tuple(findings)
