"""Loader check of the skills catalog (E30 section 8): the schema, the domain checks C1 to C5, and C6 across documents
(every cited responder flow step and every reference indicator exists in the same candidate release). A finding
rejects the candidate and the last good release stays active."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, Finding
from crew_health.kernel.errors import ProblemError
from crew_health.teach.application.content import PinnedContent, decode, parse_catalog
from crew_health.teach.domain.catalog import SkillCatalog
from crew_health.teach.domain.errors import CatalogShapeError

CHECK_ID = "teach.skills_catalog"


class SkillsCatalogCheck:
    @property
    def check_id(self) -> str:
        return CHECK_ID

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        document = candidate.documents.get(ConfigName.SKILLS_CATALOG)
        if document is None:
            return ()
        try:
            catalog = parse_catalog(document.raw)
        except CatalogShapeError as error:
            return (Finding(CHECK_ID, ConfigName.SKILLS_CATALOG, "/", error.reason),)
        return self._cross(catalog, candidate)

    @staticmethod
    def _mapping(candidate: CandidateRelease, name: ConfigName) -> Mapping[str, object]:
        document = candidate.documents.get(name)
        if document is None:
            return {}
        try:
            value = decode(document.raw)
        except ValueError:
            return {}
        return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}

    def _cross(self, catalog: SkillCatalog, candidate: CandidateRelease) -> tuple[Finding, ...]:
        content = PinnedContent(
            catalog=catalog,
            ui_copy=self._mapping(candidate, ConfigName.UI_COPY),
            indicators=self._mapping(candidate, ConfigName.INDICATOR_CATALOG),
            flows=self._mapping(candidate, ConfigName.RESPONDER_FLOWS),
        )
        findings: list[Finding] = []
        for skill_id in sorted(catalog.skills):
            skill = catalog.skills[skill_id]
            for step in skill.steps:
                if step.from_flow_step is None:
                    continue
                try:
                    content.flow_step_text(step.from_flow_step.flow_id, step.from_flow_step.index)
                except ProblemError:
                    pointer = f"/skills/{skill_id}/teach_steps/{step.step_no}/from_flow_step"
                    findings.append(
                        Finding(CHECK_ID, ConfigName.SKILLS_CATALOG, pointer, "C6 flow step not found")
                    )
            if skill.count_check is not None:
                try:
                    content.decimals(skill.count_check.reference_code)
                except ProblemError:
                    pointer = f"/skills/{skill_id}/checks/{skill.count_check.check_id}/reference"
                    findings.append(
                        Finding(CHECK_ID, ConfigName.SKILLS_CATALOG, pointer, "C6 indicator not found")
                    )
        return tuple(findings)
