"""Loader check of `participation-rules` (E22 8): a release whose document the domain parser refuses never
activates, so the last good release stays in force."""

from __future__ import annotations

import json

from crew_health.kernel.config import CandidateRelease, ConfigName, Finding
from crew_health.participation.domain.errors import InvalidRules
from crew_health.participation.domain.rules import parse_rules


class ParticipationRulesCheck:
    check_id = "participation.rules"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        view = candidate.documents.get(ConfigName.PARTICIPATION_RULES)
        if view is None:
            return (
                Finding(self.check_id, ConfigName.PARTICIPATION_RULES, "/", "participation-rules missing"),
            )
        try:
            document: object = json.loads(view.raw)
            if not isinstance(document, dict):
                raise InvalidRules("the document is not an object")
            parse_rules(document)
        except InvalidRules as error:
            return (Finding(self.check_id, ConfigName.PARTICIPATION_RULES, "/", error.reason),)
        except ValueError:
            return (Finding(self.check_id, ConfigName.PARTICIPATION_RULES, "/", "not valid JSON"),)
        return ()
