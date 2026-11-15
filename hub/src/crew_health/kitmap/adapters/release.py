"""E27 5.2 configuration adapter and 8.2 release checks.

The release check parses a candidate's kit-map.json (schema, then the domain checks B, 11, 12 and CAM) and keeps the
valid parse in a bounded cache keyed by document versions, so the request path normally never parses. A pinned or
evicted version is re-parsed from the release handle by version; a document that does not parse is never served.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable

from pydantic import ValidationError

from crew_health.contracts_gen.enums import ConfigName
from crew_health.contracts_gen.schemas.config_kit_map_schema import KitMapJson
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import UnitOfWork
from crew_health.kitmap.adapters.d22 import replace_ids, stand_ins
from crew_health.kitmap.application.errors import KitMapUnavailable
from crew_health.kitmap.application.ports import (
    ConditionRegistryPort,
    KitCheckTemplates,
    KitMapRelease,
    MissionStatePort,
)
from crew_health.kitmap.domain.document import DocumentBounds, parse_kit_map
from crew_health.kitmap.domain.model import DocumentInvalid, KitMapDoc
from crew_health.kitmap.domain.model import Finding as DomainFinding

CHECK_ID = "kitmap"
UI_COPY_CHECK = "ui_copy"
SKILLS_CHECK = "skills_catalog"
FINDING_DOCUMENTS = {UI_COPY_CHECK: ConfigName.UI_COPY, SKILLS_CHECK: ConfigName.SKILLS_CATALOG}
EXAMPLE_GUARD_ID = "kitmap.example_in_live"

type _Key = tuple[str, str, str, bool]


def _document(release: ReleaseHandle | CandidateRelease, name: ConfigName) -> ConfigDocumentView | None:
    if isinstance(release, CandidateRelease):
        return release.documents.get(name)
    try:
        return release.document(name)
    except ProblemError:
        return None


class ConfigKitMapReleaseSource:
    def __init__(
        self,
        *,
        bounds: DocumentBounds,
        environments: tuple[str, ...],
        conditions: Callable[[], ConditionRegistryPort | None],
        cache_size: int,
    ) -> None:
        if cache_size < 1:
            raise ValueError("kitmap.parsed_release_cache must be positive")
        self._bounds = bounds
        self._environments = environments
        self._conditions = conditions
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, KitMapRelease[KitMapDoc]] = OrderedDict()

    def known_conditions(self) -> frozenset[str]:
        """The 119 inventory for check 11c (CONTRACTS 11), from E10. Unavailable is a finding, never a pass."""
        registry = self._conditions()
        if registry is None:
            raise DocumentInvalid((DomainFinding("11c", "", "condition registry unavailable"),))
        try:
            return frozenset(registry.active_ids())
        except Exception:
            raise DocumentInvalid((DomainFinding("11c", "", "condition registry unavailable"),)) from None

    def parse(
        self, release: ReleaseHandle | CandidateRelease, *, known: frozenset[str] | None = None
    ) -> KitMapRelease[KitMapDoc]:
        """Raises DocumentInvalid with every finding. `known` is the 11c inventory; None only for a release that
        already passed 11c at activation (the request path re-parsing an evicted or restarted cache)."""
        kit_map = _document(release, ConfigName.KIT_MAP)
        if kit_map is None:
            raise DocumentInvalid((DomainFinding("1", "", "kit-map document missing from the release"),))
        try:
            loaded = json.loads(kit_map.raw)
            # D22: suffixed condition IDs are valid; the generated model still has the old pattern.
            candidate = json.dumps(replace_ids(loaded, stand_ins(loaded)))
        except ValueError:
            raise DocumentInvalid((DomainFinding("1", "", "not JSON"),)) from None
        try:
            KitMapJson.model_validate_json(candidate, strict=True)
        except ValidationError as error:
            pointers = tuple(
                DomainFinding("1", "/" + "/".join(str(p) for p in e["loc"]), e["type"])
                for e in error.errors()[:32]
            )
            raise DocumentInvalid(pointers) from None
        doc = parse_kit_map(kit_map.raw, self._bounds, self._environments, known)
        return KitMapRelease(
            release_id=release.release_id,
            doc=doc,
            templates=_templates(_document(release, ConfigName.UI_COPY)),
            skill_ids=_skill_ids(_document(release, ConfigName.SKILLS_CATALOG)),
            has_onboard_plans=_document(release, ConfigName.ONBOARD_PLANS) is not None,
        )

    def remember(self, parsed: KitMapRelease[KitMapDoc], key: _Key) -> None:
        self._cache[key] = parsed
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def key(self, release: ReleaseHandle | CandidateRelease) -> _Key:
        def version(name: ConfigName) -> str:
            view = _document(release, name)
            return view.version if view is not None else ""

        plans = _document(release, ConfigName.ONBOARD_PLANS) is not None
        return (
            version(ConfigName.KIT_MAP),
            version(ConfigName.UI_COPY),
            version(ConfigName.SKILLS_CATALOG),
            plans,
        )

    def for_release(self, release: ReleaseHandle) -> KitMapRelease[KitMapDoc]:
        key = self.key(release)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            if cached.release_id == release.release_id:
                return cached
            return KitMapRelease(
                release.release_id, cached.doc, cached.templates, cached.skill_ids, cached.has_onboard_plans
            )
        try:
            parsed = self.parse(release)
        except DocumentInvalid:
            raise KitMapUnavailable("the release's kit-map does not parse") from None
        self.remember(parsed, key)
        return parsed


def _sibling(view: ConfigDocumentView, check_id: str) -> object:
    """A sibling document E01 validates on its own; here an unparseable one is a finding, never an exception."""
    try:
        return json.loads(view.raw)
    except ValueError:
        raise DocumentInvalid((DomainFinding(check_id, "", "not JSON"),)) from None


def _templates(view: ConfigDocumentView | None) -> KitCheckTemplates | None:
    if view is None:
        return None
    loaded = _sibling(view, UI_COPY_CHECK)
    templates = loaded.get("kit_check_templates") if isinstance(loaded, dict) else None
    if not isinstance(templates, dict):
        return None
    can, cannot = templates.get("can"), templates.get("cannot")
    if not isinstance(can, str) or not isinstance(cannot, str) or not can or not cannot:
        return None
    return KitCheckTemplates(can=can, cannot=cannot)


def _skill_ids(view: ConfigDocumentView | None) -> frozenset[str]:
    if view is None:
        return frozenset()
    loaded = _sibling(view, SKILLS_CHECK)
    skills = loaded.get("skills") if isinstance(loaded, dict) else None
    if not isinstance(skills, list):
        return frozenset()
    return frozenset(
        s["skill_id"] for s in skills if isinstance(s, dict) and isinstance(s.get("skill_id"), str)
    )


type SourceRef = Callable[[], ConfigKitMapReleaseSource | None]
type MissionRef = Callable[[], MissionStatePort | None]
NOT_WIRED = "kit map module not resolved yet"


class KitMapReleaseCheck:
    """ReleaseCheck run by E01 at every validation (E27 8.2). A finding rejects the release; last good stays.

    The source is resolved after registration (it needs hub settings); until then every candidate is rejected.
    """

    def __init__(self, source: SourceRef) -> None:
        self._source = source

    @property
    def check_id(self) -> str:
        return CHECK_ID

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        source = self._source()
        if source is None:
            return (Finding(CHECK_ID, ConfigName.KIT_MAP, "", NOT_WIRED),)
        try:
            parsed = source.parse(candidate, known=source.known_conditions())
        except DocumentInvalid as error:
            return tuple(
                Finding(
                    f"{CHECK_ID}.{f.check_id}",
                    FINDING_DOCUMENTS.get(f.check_id, ConfigName.KIT_MAP),
                    f.pointer,
                    f.message,
                )
                for f in error.findings
            )
        source.remember(parsed, source.key(candidate))
        return ()


class ExampleInLiveGuard:
    """EX: an example document cannot be activated while the mission mode is LIVE, or while it is unknown."""

    def __init__(self, source: SourceRef, mission: MissionRef) -> None:
        self._source = source
        self._mission = mission

    @property
    def check_id(self) -> str:
        return EXAMPLE_GUARD_ID

    def check(self, uow: UnitOfWork, candidate: CandidateRelease) -> tuple[Finding, ...]:
        source = self._source()
        if source is None:
            return (Finding(EXAMPLE_GUARD_ID, ConfigName.KIT_MAP, "", NOT_WIRED),)
        try:
            doc: KitMapDoc = source.parse(candidate).doc
        except DocumentInvalid:
            return ()  # KitMapReleaseCheck reports it
        if not doc.example:
            return ()
        mission = self._mission()
        mode = mission.mission_mode(uow) if mission is not None else None
        if mode in ("REPLAY", "SIM"):
            return ()
        return (
            Finding(
                EXAMPLE_GUARD_ID, ConfigName.KIT_MAP, "/example", "example document while LIVE or unknown"
            ),
        )
