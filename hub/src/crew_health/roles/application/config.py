"""Read the roles configuration from a release (CONTRACTS 11), with the E31 loader checks R-L1 to R-L4."""

from __future__ import annotations

import json
from collections.abc import Mapping

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.roles.domain.errors import RulesInvalid
from crew_health.roles.domain.rules import (
    RolesConfig,
    check_flows,
    check_skills,
    parse_calendar,
    parse_eva_words,
    parse_practice,
    parse_role_rules,
    parse_skills,
    parse_words,
)

CACHE_MAX = 8  # releases kept parsed at once; bounded (AGENTS code rule 2)


def _doc(view: ConfigDocumentView) -> Mapping[str, object]:
    try:
        value = json.loads(view.raw)
    except (ValueError, UnicodeError) as error:
        raise RulesInvalid((("/", f"{view.name.value} is not JSON"),)) from error
    if not isinstance(value, dict):
        raise RulesInvalid((("/", f"{view.name.value} is not an object"),))
    return value


def build_config(release_id: str, documents: Mapping[ConfigName, ConfigDocumentView]) -> RolesConfig:
    def need(name: ConfigName) -> Mapping[str, object]:
        view = documents.get(name)
        if view is None:
            raise RulesInvalid((("/", f"{name.value} missing from the release"),))
        return _doc(view)

    rules = parse_role_rules(need(ConfigName.ROLE_RULES))
    skills, order = parse_skills(need(ConfigName.SKILLS_CATALOG))
    unknown_skills = check_skills(rules, frozenset(skills))
    if unknown_skills:
        raise RulesInvalid(unknown_skills)
    practice_doc = documents.get(ConfigName.PARTICIPATION_RULES)
    return RolesConfig(
        release_id=release_id,
        rules=rules,
        skills=skills,
        skill_order=order,
        calendar=parse_calendar(need(ConfigName.ROUTINE_RULES)),
        words=parse_words(need(ConfigName.UI_COPY)),
        eva_words=parse_eva_words(need(ConfigName.ENVIRONMENT_PROFILES)),
        practice=None if practice_doc is None else parse_practice(_doc(practice_doc)),
    )


_NAMES = (
    ConfigName.ROLE_RULES,
    ConfigName.SKILLS_CATALOG,
    ConfigName.UI_COPY,
    ConfigName.ROUTINE_RULES,
    ConfigName.ENVIRONMENT_PROFILES,
    ConfigName.PARTICIPATION_RULES,
)


class ConfigCache:
    """Parsed RolesConfig per release, bounded; an invalid release raises RulesInvalid and is never cached."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, ...], RolesConfig] = {}

    def get(
        self, release: ReleaseHandle, overrides: Mapping[ConfigName, ConfigDocumentView] | None = None
    ) -> RolesConfig:
        documents: dict[ConfigName, ConfigDocumentView] = {}
        for name in _NAMES:
            try:
                documents[name] = release.document(name)
            except KeyError, ProblemError:  # a document missing from the release stays missing
                continue
        documents.update(overrides or {})
        key = (release.release_id, *(f"{n.value}:{documents[n].version}" for n in _NAMES if n in documents))
        cached = self._items.get(key)
        if cached is not None:
            return cached
        built = build_config(release.release_id, documents)
        if len(self._items) >= CACHE_MAX:
            self._items.pop(next(iter(self._items)))
        self._items[key] = built
        return built


class RolesReleaseCheck:
    """E01 loader hook (design Q7): R-L1 is a warning finding, R-L2 to R-L4 and check 16 reject the release."""

    check_id = "roles.role_rules"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        try:
            config = build_config(candidate.release_id, candidate.documents)
        except RulesInvalid as invalid:
            return tuple(Finding(self.check_id, ConfigName.ROLE_RULES, p, m) for p, m in invalid.findings)
        flows = candidate.documents.get(ConfigName.RESPONDER_FLOWS)
        if flows is None:
            return ()
        try:
            flow_ids = [str(f["id"]) for f in json.loads(flows.raw)["flows"]]
        except ValueError, KeyError, TypeError:
            return (Finding(self.check_id, ConfigName.RESPONDER_FLOWS, "/flows", "unreadable flows"),)
        return tuple(
            Finding("roles.r_l1_warning", ConfigName.ROLE_RULES, "/flow_roles", f"{flow} uses default_roles")
            for flow in check_flows(config.rules, flow_ids)
        )
