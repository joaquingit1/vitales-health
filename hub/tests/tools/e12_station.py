"""E12 on the real composed hub: E01's create_app over a temporary SQLite directory with E02's identity and consent,
E27's guidance and E12's tools module installed, built on E02's station (hub/tests/privacy/e02_station.py).

The gateway, its audit, scope authority, catalog compilation, policy pinning and clock seam are production code
resolved by port class from the running app. What is not on main is supplied through the composer as test doubles
of the owners' canonical ports, never as hub service replacements:
- E10's EvidenceQueryPort (wave 2 canonical ports, 728c2b7) and E10's ContentFacts answer for one pinned release;
- E17's ModelReleaseReader and TokenCounter serve the package's closed tool catalog (docs/product/tool-catalog.json)
  with each tool exposed in its own stage;
- E23's EnvironmentFacts and E31's SkillFacts answer for the SIM roster.
Every double's data is built here from the package examples; no clinical content is invented.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from crew_health.app.assembly import BuildCatalog
from crew_health.app.registry import ModuleRegistry
from crew_health.app.station import HubResources, bootstrap_release, config_artifacts
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.domain.templates import TemplateCheck
from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.events.module import EventAccess
from crew_health.guidance.application.ports import (
    ContentFacts,
    ContentFactSet,
    ContentRef,
    EnvironmentFacts,
    Fact,
    Known,
    OperatorFact,
    SkillFacts,
)
from crew_health.identity.application.ports import IdentityPorts
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ReleaseCheck
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.testing import MemoryClock, MemoryReleaseCheck
from crew_health.kernel.uow import ReadSnapshot, SessionContext
from crew_health.knowledge.application.ports import (
    EvidenceQueryPort,
    dto,  # type: ignore[attr-defined]
)
from crew_health.model.application.ports import (
    ContextSettings,
    ControllerSettings,
    EmbeddingPin,
    ModelRelease,
    ModelReleaseReader,
    ModelReleaseRef,
    RetrievalSettings,
    RuntimeSettings,
    ToolCatalog,
)
from crew_health.model.application.ports import TokenCounter as ModelTokenCounter
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy"))

import e02_station  # noqa: E402
from e02_station import (  # noqa: E402
    MODULES,
    PRODUCT,
    ROOT,
    SOURCE,
    START,
    E02Station,
    mission_id,
)

E12_MODULES = (*MODULES, "guidance", "tools")
MODEL_RELEASE = "model_sim_stub"
EVIDENCE_RELEASE = "evr_sim_a"
USE = "risk_context"


def member(
    passage_id: str,
    release_id: str = EVIDENCE_RELEASE,
    *,
    text: str = "Rest and fluids; seek review if the headache worsens.",
    status: str = "approved",
) -> dto.ReleaseMembership:
    digest = hashlib.sha256(text.encode()).hexdigest()
    artifact = dto.PassageArtifact(passage_id, "ext_sim", 0, len(text), digest, text, "Headache", 3)
    return dto.ReleaseMembership(
        release_id,
        artifact,
        ("src_sim",),
        "public_domain",
        "not_clinically_reviewed",
        ((USE, status),),
        (),
        digest,
    )


class Evidence:
    """Canonical EvidenceQueryPort double: rows keyed by (release, passage); history across releases; history
    pages by the last release_id returned; discrepancies page with next_cursor."""

    def __init__(self, *rows: dto.ReleaseMembership) -> None:
        self.rows = {(row.release_id, row.passage.passage_id): row for row in rows}
        self.discrepancy_items: tuple[dto.Discrepancy, ...] = ()
        self.neighbors: tuple[str, ...] = ()
        self.searched: list[str] = []
        self.reads: list[str] = []
        self.unavailable = False

    def active_release(self) -> dto.ActivePointer | dto.NoActiveRelease:
        releases = sorted({release for release, _ in self.rows})
        return dto.ActivePointer(releases[-1], 1) if releases else dto.NoActiveRelease()

    def passages_for_sources(
        self, release_id: str, source_ids: Sequence[str], use: str, filters: object, limit: int
    ) -> dto.SearchResult:
        return dto.NoMatch(release_id)

    def pathway_card(self, release_id: str, condition_id: str) -> dto.CardRead:
        return dto.NotInRelease(release_id, condition_id)

    def history_of(self, passage_id: str) -> list[dto.ReleaseMembership]:
        rows = (row for (_, pid), row in self.rows.items() if pid == passage_id)
        return sorted(rows, key=lambda row: row.release_id)

    def read_approved(self, release_id: str, passage_id: str, use: str) -> dto.ApprovedRead:
        self.reads.append(passage_id)
        if self.unavailable:
            return dto.ReleaseUnavailable(release_id, "storage")
        row = self.rows.get((release_id, passage_id))
        if row is None:
            if self.history_of(passage_id):
                return dto.InReleaseHistory(release_id, passage_id)
            return dto.NotInRelease(release_id, passage_id)
        if dict(row.use_status).get(use) != "approved":
            return dto.NotApprovedForUse(release_id, passage_id, use)
        return dto.Eligible(row, release_id, use)

    def inspect_history(self, passage_id: str, cursor: str | None, limit: int) -> dto.HistoryRead:
        rows = [row for row in self.history_of(passage_id) if cursor is None or row.release_id > cursor]
        return tuple(rows[:limit])

    def _hits(self, release_id: str, ids: Sequence[str]) -> dto.SearchResult:
        items = tuple(
            dto.RankedPassage(rank, "lexical", self.rows[(release_id, pid)])
            for rank, pid in enumerate(ids, 1)
        )
        return dto.Hits(release_id, items) if items else dto.NoMatch(release_id)

    def lexical_search(
        self, release_id: str, query: str, use: str, filters: object, mode: str, k: int
    ) -> dto.SearchResult:
        self.searched.append(query)
        current = sorted(pid for rid, pid in self.rows if rid == release_id)
        return self._hits(release_id, current[:k])

    def dense_search(
        self, release_id: str, query: str, use: str, filters: object, k: int
    ) -> dto.SearchResult:
        self.searched.append(query)
        return dto.IndexUnavailable(release_id, "dense", "not_built")

    def expand_context(
        self, release_id: str, passage_id: str, use: str, filters: object, neighbors: int
    ) -> dto.SearchResult:
        return self._hits(release_id, (passage_id, *self.neighbors[: neighbors * 2]))

    def discrepancies(self, release_id: str, cursor: str | None, limit: int) -> dto.DiscrepancyPage:
        start = 0 if cursor is None else int(cursor.removeprefix("page_"))
        items = self.discrepancy_items[start : start + limit]
        more = start + limit < len(self.discrepancy_items)
        return dto.DiscrepancyPage(items, f"page_{start + limit}" if more else None)


@dataclass
class Content:
    """E10's ContentFacts for the Evidence double's rows: a row's own approval, review and simulation flags."""

    evidence: Evidence

    def content(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet:
        rows = [row for (_, pid), row in self.evidence.rows.items() if pid == content.id]
        row = rows[0] if len(rows) == 1 else None
        valid = row is not None and row.passage.text_sha256 == content.content_sha256
        status = None if row is None else dict(row.use_status).get(purpose)
        return ContentFactSet(
            Known(valid),
            Known(status == "approved"),
            "not_clinically_reviewed" if row is None else row.clinical_review,
            Known(status == "withdrawn"),
            Known(row is not None and row.simulation_only),
            True,
            Known(frozenset()),
            None,
            None,
        )


class Places:
    """E23's EnvironmentFacts: content naming no environment applies everywhere."""

    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Fact[bool]:
        return Known(not environments)


class Crew:
    """E31's SkillFacts for the SIM roster: everyone awake and on site, no skill records."""

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Known(
            tuple(
                OperatorFact(
                    item.person_id, item.roster_index, True, True, False, item.medical_officer_rank, {}
                )
                for item in e02_station.sim_members()
            )
        )


def product_catalog() -> ToolCatalog:
    """The package's closed tool catalog, each tool exposed in its own stage (E17's grouping is not on main)."""
    document = json.loads((PRODUCT / "tool-catalog.json").read_text(encoding="utf-8"))
    tools = tuple(document["tools"])
    exposure: dict[str, tuple[str, ...]] = {}
    for tool in tools:
        exposure[tool["stage"]] = (*exposure.get(tool["stage"], ()), tool["name"])
    return ToolCatalog(tools, exposure)


@dataclass
class Models:
    """E17's ModelReleaseReader for one stub release. Retrieval values sit inside E12's hub bounds; they are a
    double's inputs, not tuned settings."""

    active_release: str | None = MODEL_RELEASE
    retrieval: Mapping[str, object] = field(
        default_factory=lambda: {
            "k_lexical": 3,
            "k_dense": 0,
            "final_min": 1,
            "final_max": 3,
            "budget_tokens": 4096,
            "max_per_parent": 2,
            "k_rrf": 60,
            "rerank": False,
        }
    )

    def active(self) -> ModelRelease | None:
        if self.active_release is None:
            return None
        return ModelRelease(canonical_json({"release_id": self.active_release, "mode": "stub"}))

    def get(self, release_id: str) -> ModelRelease:
        return self.load(release_id)

    def load(self, release_id: str) -> ModelReleaseRef:
        if release_id != self.active_release:
            raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
        return ModelReleaseRef(canonical_json({"release_id": release_id, "mode": "stub"}))

    def tool_catalog(self, model_release_id: str) -> ToolCatalog:
        return product_catalog()

    def context_settings(self, model_release_id: str) -> ContextSettings:
        return ContextSettings({})

    def runtime_settings(self, model_release_id: str) -> RuntimeSettings:
        return RuntimeSettings({})

    def controller_settings(self, model_release_id: str) -> ControllerSettings:
        return ControllerSettings({})

    def embedding_pin(self) -> EmbeddingPin:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)

    def retrieval_settings(self, model_release_id: str) -> RetrievalSettings:
        return RetrievalSettings(dict(self.retrieval))  # type: ignore[arg-type]


class Tokens:
    """E17's TokenCounter double: one token per UTF-8 byte, never fewer than the real tokenizer would count."""

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))

    def count_request(self, body: Mapping[str, object]) -> int:
        return len(canonical_json(body))


def e12_settings(**overrides: int) -> ValidatedSettings:
    from crew_health.guidance.module import setting_specs as guidance_specs
    from crew_health.tools.settings import setting_specs as tools_specs

    base = e02_station.station_settings()
    extra = (*guidance_specs(), *tools_specs())
    specs = (*base._specs.values(), *extra)
    values = {**base._values, **{spec.key: int(spec.maximum or 0) for spec in extra}}
    values.update(overrides)
    return ValidatedSettings(specs, values)


def e12_build(directory: Path) -> BuildCatalog:
    target = directory / "packaged/crew_health"
    for module in E12_MODULES:
        path = target / module
        path.mkdir(parents=True, exist_ok=True)
        for name in ("__init__.py", "module.py"):
            shutil.copyfile(SOURCE / module / name, path / name)
    from crew_health.clock.module import register as clock
    from crew_health.config.module import register as config
    from crew_health.consent.module import register as consent
    from crew_health.events.module import register as events
    from crew_health.guidance.module import register as guidance
    from crew_health.identity.module import register as identity
    from crew_health.platform.module import register as platform
    from crew_health.records.module import register as records
    from crew_health.tools.module import register as tools

    registrations = [clock, config, consent, events, guidance, identity, platform, records, tools]
    inventory = {
        fn.__module__: hashlib.sha256(
            (target / fn.__module__.split(".")[1] / "module.py").read_bytes()
        ).hexdigest()
        for fn in registrations
    }
    return BuildCatalog(target, inventory, tuple(registrations))


class _OwnChecksFirst:
    """E02's station composer predates E01 #41, where the config module registers every package check itself and
    the assembly provides EventAccess; the composer's stand-ins for those and its unverified ConfigArtifacts are
    skipped so E01's own run (artifacts from E01's production `config_artifacts`), and the session authorizer E01
    asks for is E02's live-session reader."""

    def __init__(self, registry: ModuleRegistry) -> None:
        self._registry = registry

    def loader_check(self, check: ReleaseCheck) -> None:
        declared = getattr(self._registry, "declarations", {}).get("loader_checks", {})
        if check.check_id not in declared:
            self._registry.loader_check(check)

    def provide(
        self, port: type[object], factory: object, *, requires: tuple[type[object], ...] = ()
    ) -> None:
        if port not in (EventAccess, ConfigArtifacts):
            self._registry.provide(port, factory, requires=requires)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._registry, name)


@dataclass
class E12Station(E02Station):
    evidence: Evidence = field(default_factory=lambda: Evidence(member("psg_sim_a")))
    models: Models | None = field(default_factory=Models)
    tokens: Tokens | None = field(default_factory=Tokens)

    def configure(self, registry: ModuleRegistry) -> None:
        super().configure(_OwnChecksFirst(registry))
        artifacts = config_artifacts(HubResources(ROOT))
        registry.provide(ConfigArtifacts, lambda resolver: artifacts)
        registry.provide(
            SessionAuthorizer,  # type: ignore[type-abstract]
            lambda resolver: resolver.require(IdentityPorts).sessions,
            requires=(IdentityPorts,),
        )
        evidence, models, tokens = self.evidence, self.models, self.tokens
        registry.provide(EvidenceQueryPort, lambda resolver: evidence)  # type: ignore[type-abstract]
        registry.provide(ContentFacts, lambda resolver: Content(evidence))  # type: ignore[type-abstract]
        registry.provide(EnvironmentFacts, lambda resolver: Places())  # type: ignore[type-abstract]
        registry.provide(SkillFacts, lambda resolver: Crew())  # type: ignore[type-abstract]
        if models is not None:
            registry.provide(ModelReleaseReader, lambda resolver: models)  # type: ignore[type-abstract]
        if tokens is not None:
            registry.provide(ModelTokenCounter, lambda resolver: tokens)  # type: ignore[type-abstract]


def install_release(db: SQLiteDatabase) -> None:
    """E01's first-start release from the repository configuration (app.station.bootstrap_release), activated
    through the configuration service with E01's verified artifacts; E02's station release predates E01 #41's
    package checks, which its example plans do not pass."""
    e02_station.migrate_core(db, ("config", "clock", "events", "records"))
    db.event_sink = EventRepository(
        settings=db.settings, ids=db.ids, validator=EventValidator(schema_directory=e02_station.SCHEMAS)
    ).append
    resources = HubResources(ROOT)
    artifacts = config_artifacts(resources)
    checks = LoaderChecks(maximum=1024)
    for check in (
        artifacts.schemas,
        artifacts.sources,
        artifacts.indicators,
        TemplateCheck(),
        *artifacts.package,
    ):
        checks.register(check)
    for identifier in sorted(required_contributors() - set(checks.checks)):
        checks.register(MemoryReleaseCheck(identifier, lambda candidate: ()))
    service = ReleaseActivationService(
        ConfigRepository(),
        ConfigLoader(checks, max_document_bytes=db.settings.integer("commands.max_bytes")),
        db.ids,
        origin=lambda view: "SIM",
    )
    candidate, _ = bootstrap_release(resources, db.ids)
    editor = SessionContext("session_profile", "editor", e02_station.OPERATOR, None, frozenset({Role.EDITOR}))
    with db.begin(actor=e02_station.OPERATOR, correlation_id="sim_profile_install") as uow:
        service.propose(uow, editor, candidate)
        service.activate(
            uow, editor, release_id=candidate.release_id, expected_etag=f'"{candidate.release_id}:1:0"'
        )
    db.set_release_reader(lambda view: SQLiteConfigReader(view, ConfigRepository()).active())


def e12_station(directory: Path, **overrides: object) -> E12Station:
    clock = MemoryClock(START, 1_000_000_000, "epoch_first")
    db = database(directory / "state", clock=clock)
    db.settings = e12_settings()
    install_release(db)
    e02_station.install_e02(db)
    clocks = e02_station._sim_clock(db)
    return E12Station(directory, db, clock, e12_build(directory), clocks, None, **overrides)  # type: ignore[arg-type]


def tool_scope(
    session: SessionContext,
    *,
    phase: Phase = Phase.ASSESSMENT,
    turn: str = "turn_sim_a",
    release: str,
    seconds: int = 60,
    model: str = MODEL_RELEASE,
) -> ToolScope:
    """The controller's turn scope for the signed-in crew member, anchored on the SIM station clock."""
    anchor = TimeInput(START, "sim_source", "sim_segment", 1, "hub_corrected", "SIM")
    return ToolScope(
        session,
        session.person_id,
        mission_id(),
        "SIM",
        None,
        None,
        None,
        turn,
        "conv_sim_a",
        release,
        EVIDENCE_RELEASE,
        model,
        phase,
        1,
        1,
        ElapsedDeadline(anchor, timedelta(seconds=seconds)),
    )
