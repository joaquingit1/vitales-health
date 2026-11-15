"""The supplies view of one configuration release (E32 8.1), built once per release and cached (bounded).

Also the pure release check (S-L1 to S-L7) E01 runs on every candidate, and the activation guard (S-A1 to S-A3) it
runs inside the activation transaction."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.supplies.application.ports import MissionStatePort
from crew_health.supplies.application.repository import LedgerRepository
from crew_health.supplies.domain.applicability import applicable_needs
from crew_health.supplies.domain.config_checks import release_findings
from crew_health.supplies.domain.document import (
    Issue,
    KitSupplies,
    Plan,
    PlanStep,
    read_kit_supplies,
    read_plans,
    read_threshold,
    read_words,
    resolve_needs,
)
from crew_health.supplies.domain.errors import DocumentInvalid
from crew_health.supplies.domain.guard import guard_findings
from crew_health.supplies.domain.model import Need, SupplyDef, SupplyWords, Threshold

CHECK_ID = "supplies.release"
GUARD_ID = "supplies.stock_guard"


@dataclass(frozen=True, slots=True)
class ReleaseIndex:
    release_id: str
    kit_map_version: str  # served version (SHA-256 of the file)
    plans_version: str | None
    kit: KitSupplies
    supplies: Mapping[str, SupplyDef]
    steps: tuple[tuple[Plan, PlanStep, tuple[Need, ...]], ...]
    threshold: Threshold | None  # None: no warning-rules document, so nothing is low
    words: SupplyWords

    def needs_in(self, environment_id: str) -> Mapping[str, tuple[Need, ...]]:
        return applicable_needs(self.steps, environment_id)


def _decode(view: ConfigDocumentView) -> object:
    try:
        return json.loads(view.raw, parse_float=Decimal)
    except ValueError as error:
        raise DocumentInvalid(view.name.value, "", "not JSON") from error


def _optional(release: ReleaseHandle | CandidateRelease, name: ConfigName) -> ConfigDocumentView | None:
    if isinstance(release, CandidateRelease):
        return release.documents.get(name)
    try:
        return release.document(name)
    except ProblemError:
        return None


def _first(issues: tuple[Issue, ...]) -> None:
    if issues:
        raise DocumentInvalid(issues[0].document, issues[0].pointer, issues[0].message)


def build_index(release: ReleaseHandle) -> ReleaseIndex:
    """Raises DocumentInvalid at the first problem: a release this module cannot read serves no cover at all."""
    kit_view = _optional(release, ConfigName.KIT_MAP)
    if kit_view is None:
        raise DocumentInvalid("kit-map", "", "the release has no kit map")
    kit, issues = read_kit_supplies(_decode(kit_view))
    _first(issues)
    supplies = {s.supply_id: s for s in kit.supplies}
    plans_view = _optional(release, ConfigName.ONBOARD_PLANS)
    steps: tuple[tuple[Plan, PlanStep, tuple[Need, ...]], ...] = ()
    if plans_view is not None:
        plans, plan_issues = read_plans(_decode(plans_view))
        _first(plan_issues)
        steps, need_issues = resolve_needs(plans, supplies)
        _first(need_issues)
    threshold: Threshold | None = None
    rules_view = _optional(release, ConfigName.WARNING_RULES)
    if rules_view is not None:
        threshold, rule_issues = read_threshold(_decode(rules_view))
        _first(rule_issues)
    copy_view = _optional(release, ConfigName.UI_COPY)
    if copy_view is None:
        raise DocumentInvalid("ui-copy", "/supply_words", "the release has no ui-copy")
    words, word_issues = read_words(_decode(copy_view))
    _first(word_issues)
    if words is None:
        raise DocumentInvalid("ui-copy", "/supply_words", "no supply words")
    return ReleaseIndex(
        release_id=release.release_id,
        kit_map_version=kit_view.version,
        plans_version=None if plans_view is None else plans_view.version,
        kit=kit,
        supplies=supplies,
        steps=steps,
        threshold=threshold,
        words=words,
    )


class ReleaseIndexCache:
    """At most `capacity` parsed releases, least recently used evicted; a release ID never changes content."""

    def __init__(self, capacity: int, builder: Callable[[ReleaseHandle], ReleaseIndex] = build_index) -> None:
        if capacity < 1:
            raise ValueError("cache capacity is at least 1")
        self._capacity, self._builder = capacity, builder
        self._items: OrderedDict[str, ReleaseIndex] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, release: ReleaseHandle) -> ReleaseIndex:
        with self._lock:
            found = self._items.get(release.release_id)
            if found is not None:
                self._items.move_to_end(release.release_id)
                return found
        index = self._builder(release)
        with self._lock:
            self._items[release.release_id] = index
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)
        return index


def _finding(issue: Issue) -> Finding:
    names = {n.value: n for n in ConfigName}
    return Finding(f"{CHECK_ID}.{issue.check_id}", names.get(issue.document), issue.pointer, issue.message)


class SuppliesReleaseCheck:
    """E01 loader check (kernel ReleaseCheck): pure over the candidate's documents."""

    @property
    def check_id(self) -> str:
        return CHECK_ID

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        kit = candidate.documents.get(ConfigName.KIT_MAP)
        if kit is None:
            return ()
        try:
            decoded = {
                name: (None if view is None else _decode(view))
                for name, view in (
                    (n, candidate.documents.get(n))
                    for n in (
                        ConfigName.KIT_MAP,
                        ConfigName.ONBOARD_PLANS,
                        ConfigName.WARNING_RULES,
                        ConfigName.UI_COPY,
                        ConfigName.SKILLS_CATALOG,
                    )
                )
            }
        except DocumentInvalid as error:
            names = {n.value: n for n in ConfigName}
            return (Finding(CHECK_ID, names.get(error.document), error.pointer, str(error)),)
        issues = release_findings(
            decoded[ConfigName.KIT_MAP],
            decoded[ConfigName.ONBOARD_PLANS],
            decoded[ConfigName.WARNING_RULES],
            decoded[ConfigName.UI_COPY],
            decoded[ConfigName.SKILLS_CATALOG],
        )
        return tuple(_finding(i) for i in issues)


class SuppliesActivationGuard:
    """E01 activation guard: in the activation transaction, a candidate that would make stock negative or change a
    used supply's unit is rejected and the last good release stays active (S-A1 to S-A3, SYSTEM 5.6)."""

    def __init__(
        self,
        repository: Callable[[], LedgerRepository | None],
        mission: Callable[[], MissionStatePort | None],
        limit: Callable[[], int],
    ) -> None:
        self._repository, self._mission, self._limit = repository, mission, limit

    @property
    def check_id(self) -> str:
        return GUARD_ID

    def check(self, uow: UnitOfWork, candidate: CandidateRelease) -> tuple[Finding, ...]:
        view = candidate.documents.get(ConfigName.KIT_MAP)
        if view is None:
            return ()
        repository = self._repository()
        if repository is None:
            return (
                Finding(GUARD_ID, ConfigName.KIT_MAP, "", "supplies ledger not wired: cannot guard stock"),
            )
        try:
            kit, issues = read_kit_supplies(_decode(view))
        except DocumentInvalid as error:
            return (Finding(GUARD_ID, ConfigName.KIT_MAP, error.pointer, str(error)),)
        if issues:
            return tuple(_finding(i) for i in issues)
        mission = _current_mission(self._mission(), uow)
        limit = self._limit()
        totals = repository.totals(uow, mission_id=mission, limit=limit)
        units = repository.registered_units(uow, limit=limit)
        return tuple(
            Finding(f"{GUARD_ID}.{i.check_id}", ConfigName.KIT_MAP, i.pointer, i.message)
            for i in guard_findings(kit, units, totals)
        )


def _current_mission(port: MissionStatePort | None, view: UnitOfWork | ReadSnapshot) -> str | None:
    """The current mission's ID; None when unknown, and then the guard checks every mission (stricter, fail safe)."""
    if port is None:
        return None
    facts = port.current(view)
    return None if facts is None else facts.mission_id
