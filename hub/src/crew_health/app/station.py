"""The production composition of one station hub (SYSTEM 5.12, DEPLOYMENT 7.1): where the hub finds its frozen
contracts, offline package artifacts and repository configuration, the first-start configuration release built from
them with its provenance, and the fail-closed stand-ins for ports whose providing epic is not on main yet.

The resource root is the repository checkout when running from source and the `crew_health/resources` package data
of an installed wheel; nothing is looked up relative to the installed package's parents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from crew_health.app.registry import ModuleRegistry

if TYPE_CHECKING:
    from crew_health.app.camera_reports import HubReports
    from crew_health.app.demo_mission import DemoMission
    from crew_health.app.privacy import DemoOperatorRecord
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.domain.package_checks import saved_form_pages
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.kernel.commands import ResultVisibility, canonical_json
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import ReadSnapshot, SessionContext
from crew_health.kitmap.application.ports import ConditionRegistryPort
from crew_health.kitmap.application.ports import MissionStatePort as KitMission
from crew_health.model.application.ports import ModelInstallation, StubInstallation
from crew_health.roles.api.router import RequestAuthenticator as RolesAuthenticator
from crew_health.roles.application.ports import DayLabelPort, MissionStatePort, OverlayPort, RosterPort
from crew_health.teach.application.ports import PersonDirectory, RecordRouting
from crew_health.teach.application.ports import RequestAuthenticator as TeachAuthenticator

type Origin = Literal["LIVE", "REPLAY", "SIM"]  # internal enum

PRODUCT_ARTIFACTS = (
    "input-dictionary.json",
    "sources.csv",
    "NASA-SOURCES.md",
    "DEMO-CONDITIONS.md",
    "RESEARCH-ANNEX.md",
    "environment-hazards.json",
    "routine-schedule.json",
    "responder-flows.json",
)
PACKAGE_MANIFEST = "package-manifest.json"
CONDITION_REGISTRY = "condition-assessments-119.json"
SOURCE_DOCUMENTS = {
    f"api/examples/config/{name.value}.example.json": name
    for name in (ConfigName.SKILLS_CATALOG, ConfigName.ONBOARD_PLANS)
}
# Documents the frozen package ships as files rather than examples.
PACKAGE_DOCUMENTS = frozenset({ConfigName.ENVIRONMENT_HAZARDS, ConfigName.ROUTINE_SCHEDULE})
MAX_RESOURCE_BYTES = 8 * 1024 * 1024


def _unavailable() -> ProblemError:
    return ProblemError(
        ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value, retryable=False
    )


def _read(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_RESOURCE_BYTES + 1)
    except OSError:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "hub resource unavailable") from None
    if len(raw) > MAX_RESOURCE_BYTES:
        raise ProblemError(ErrorCode.CONFIG_INVALID, "hub resource exceeds its bound")
    return raw


@dataclass(frozen=True, slots=True)
class HubResources:
    """The read-only files a station hub serves and validates against. `root` holds `contracts/` (the frozen API
    package with its SHA256SUMS and decision overlay), `config/` (the repository configuration) and the offline
    package artifacts, under `docs/product/` in a checkout or `product/` in an installed wheel."""

    root: Path

    @property
    def contracts(self) -> Path:
        return self.root / "contracts"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def product(self) -> Path:
        packaged = self.root / "product"
        return packaged if packaged.is_dir() else self.root / "docs/product"

    @classmethod
    def locate(cls) -> HubResources:
        """The installed package data when present, else the source checkout this module was imported from."""
        packaged = Path(str(files("crew_health"))) / "resources"
        if (packaged / "contracts/api/openapi.yaml").is_file():
            return cls(packaged)
        checkout = Path(__file__).resolve().parents[4]
        if (checkout / "contracts/api/openapi.yaml").is_file() and (
            checkout / "hub/pyproject.toml"
        ).is_file():
            return cls(checkout)
        raise ProblemError(ErrorCode.CONFIG_INVALID, "hub resources not found")


def config_artifacts(resources: HubResources) -> ConfigArtifacts:
    """Loader checks 1, 2 and 9 over the frozen schemas and the offline package sources, each artifact verified
    against the package manifest hash before use."""
    manifest = json.loads(_read(resources.product / PACKAGE_MANIFEST))
    hashes = {str(row["path"]): str(row["sha256"]) for row in manifest["files"]}
    raw = {name: _read(resources.product / name) for name in PRODUCT_ARTIFACTS}
    raw.update({path: _read(resources.contracts / path) for path in SOURCE_DOCUMENTS})
    if not raw.keys() <= hashes.keys():
        raise ProblemError(ErrorCode.CONFIG_INVALID, "package manifest lacks an artifact")
    return ConfigArtifacts.from_verified(
        schema_directory=resources.contracts / "api/schemas",
        artifacts=raw,
        inventory={name: hashes[name] for name in raw},
        source_documents=SOURCE_DOCUMENTS,
        condition_ids=PackageConditions.load(resources).ids,
        form_pages=saved_forms(resources),
    )


def saved_forms(resources: HubResources) -> dict[str, tuple[str, ...]]:
    """The saved CLIFF forms check 14 quotes against, keyed by source URL. The package manifest does not list them
    (its cliff/manifest.jsonl hashes the PDFs), so they are read as shipped; a missing directory leaves check 14
    with no forms, which refuses every quoted plan."""
    directory = resources.product / "cliff/forms"
    forms = (saved_form_pages(_read(path)) for path in sorted(directory.glob("*.json")))
    return dict(forms)


def _verified(resources: HubResources, name: str) -> bytes:
    manifest = json.loads(_read(resources.product / PACKAGE_MANIFEST))
    hashes = {str(row["path"]): str(row["sha256"]) for row in manifest["files"]}
    raw = _read(resources.product / name)
    if hashes.get(name) != hashlib.sha256(raw).hexdigest():
        raise ProblemError(ErrorCode.CONFIG_INVALID, "package artifact hash mismatch")
    return raw


@dataclass(frozen=True, slots=True)
class PackageConditions:
    """E01's offline condition registry (SYSTEM 5.12, the equivalent of E10 checks 2 and 11): the condition IDs of
    the package's verified condition-assessments-119.json, loaded once at startup."""

    ids: frozenset[str]

    def active_ids(self) -> frozenset[str]:
        return self.ids

    @classmethod
    def load(cls, resources: HubResources) -> PackageConditions:
        rows = json.loads(_verified(resources, CONDITION_REGISTRY))
        return cls(frozenset(str(row["condition_id"]) for row in rows))


@dataclass(frozen=True, slots=True)
class StationMission:
    """Mission facts a station knows before E22 lands: its origin mode. The active environment stays unknown."""

    origin: Origin

    def mission_mode(self, uow: object = None) -> Origin:
        return self.origin

    def active_environment_id(self, uow: object = None) -> str | None:
        return None


def basis_summaries(resources: HubResources) -> dict[ConfigName, str]:
    index = json.loads(_read(resources.contracts / "api/examples/configuration/index.json"))
    summaries = {ConfigName(row["name"]): str(row["basis_summary"]) for row in index["value"]["documents"]}
    if summaries.keys() != set(ConfigName):
        raise ProblemError(ErrorCode.CONFIG_INVALID, "configuration index incomplete")
    return summaries


def _document(resources: HubResources, name: ConfigName) -> tuple[bytes, dict[str, str]]:
    """One first-start document and its provenance: the repository's config/ file when it has one, else the frozen
    package file or the `value` of its package example."""
    repository = resources.config / (name.value + ".json")
    if repository.is_file():
        raw = _read(repository)
        return raw, {"path": "config/" + repository.name, "sha256": hashlib.sha256(raw).hexdigest()}
    if name in PACKAGE_DOCUMENTS:
        raw = _read(resources.product / (name.value + ".json"))
        return raw, {
            "path": "docs/product/" + name.value + ".json",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    relative = f"api/examples/config/{name.value}.example.json"
    source = _read(resources.contracts / relative)
    raw = json.dumps(json.loads(source)["value"]).encode()
    return raw, {
        "path": "contracts/" + relative,
        "sha256": hashlib.sha256(source).hexdigest(),
        "pointer": "/value",
    }


def bootstrap_release(resources: HubResources, ids: IdFactory) -> tuple[CandidateRelease, str]:
    """The first-start release: every document with its source path and hash. Its ID derives from the document
    versions, so the same resources always propose the same release. The provenance travels as the release note
    until R-SYS-14 adds a wire field for it."""
    documents: dict[ConfigName, ConfigDocumentView] = {}
    provenance: dict[str, dict[str, str]] = {}
    for name in ConfigName:
        raw, source = _document(resources, name)
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
        provenance[name.value] = source
    digest = hashlib.sha256(
        "".join(name.value + documents[name].version for name in ConfigName).encode()
    ).hexdigest()
    note = canonical_json({"provenance": "first_start", "documents": provenance}).decode()
    return CandidateRelease(ids.derived("release", digest), documents), note


class Unavailable:
    """A port whose providing epic is not on main yet. Every call fails closed with CAPABILITY_UNAVAILABLE, so the
    consuming module composes and its routes answer with the typed fault instead of the hub failing to start."""

    def __init__(self, port: type[object]) -> None:
        self.port = port.__qualname__

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        if name.startswith("__"):
            raise AttributeError(name)

        def refuse(*args: object, **kwargs: object) -> NoReturn:
            raise _unavailable()

        return refuse


@dataclass(frozen=True, slots=True)
class NoMissionFacts:
    """SYSTEM 4 MissionStatePort before E22/E23 land: no mission facts are known, which the port reports as None
    (consumers already handle it, e.g. plans checks a release as LIVE). Day boundaries need the mission's day
    length, so asking for one fails closed."""

    def current(self, uow: object = None) -> None:
        return None

    def day_end(self, instant: datetime) -> NoReturn:
        raise _unavailable()


@dataclass(frozen=True, slots=True)
class ClosedSessions:
    """A hub built without the E02 identity module: no event stream session is ever alive."""

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        return False


# Ports a module on main requires whose provider belongs to an epic that has not landed (E02 sessions and
# authentication, E04 roster and overlays, E22 day labels, E30 directory and routing). Mission state has its own
# answer (NoMissionFacts).
PENDING_PORTS: tuple[type[object], ...] = (
    cast(type[object], RosterPort),
    cast(type[object], OverlayPort),
    cast(type[object], DayLabelPort),
    cast(type[object], RolesAuthenticator),
    cast(type[object], PersonDirectory),
    cast(type[object], RecordRouting),
    cast(type[object], TeachAuthenticator),
)


def _constant(value: object, resolver: object) -> object:
    return value


@dataclass(frozen=True, slots=True)
class StationMetadata:
    basis_summaries: Mapping[ConfigName, str]
    origin: Origin

    def origin_of(self, view: ReadSnapshot) -> Origin:
        return self.origin


def _demo_mission(registry: ModuleRegistry, mission: DemoMission) -> None:
    """The demo profile's mission facts under every consumer's MissionStatePort class (app/demo_mission.py)."""
    from dataclasses import replace

    from crew_health.app.registry import DeclarationRegistry
    from crew_health.camera.application.ports import MissionStatePort as CameraMission
    from crew_health.clock.application.clock import ClockIdentity
    from crew_health.groundlink.module import provide_mission as ground_mission
    from crew_health.help.application.ports import MissionStatePort as HelpMission
    from crew_health.kernel.met import MissionClock
    from crew_health.measurement.application.ports import MissionStatePort as MeasurementMission
    from crew_health.records.application.seams import MissionStatePort as RecordsMission

    clock = cast(type[object], MissionClock)

    def bound(resolver: object) -> object:
        return replace(
            mission,
            site_node_id=resolver.require(ClockIdentity).node_id,  # type: ignore[attr-defined]
            clock=resolver.require(MissionClock),  # type: ignore[attr-defined]
        )

    for port in (
        CameraMission,
        HelpMission,
        MeasurementMission,
        RecordsMission,
        KitMission,
        MissionStatePort,
        OverlayPort,
    ):
        # E22/E23's mission module declares some of these consumer ports too. The demo profile keeps its own
        # mission facts there (its cues write the environment and the surface walk overlays), so it replaces them.
        if isinstance(registry, DeclarationRegistry) and cast(type[object], port) in registry.providers:
            registry.replace(cast(type[object], port), bound, requires=(ClockIdentity, clock))
        else:
            registry.provide(cast(type[object], port), bound, requires=(ClockIdentity, clock))
    ground_mission(registry, bound, requires=(ClockIdentity, clock))


def _camera_f1(registry: ModuleRegistry, provided: Mapping[object, object], reports: object | None) -> None:
    """E06's F1 binding (app/camera_reports.py): E01's task ports from the platform TaskService, the camera's
    readiness and assignment under E05's port classes, and, when the profile names the hub listener, the attempt
    reports over E05's recordAttempt route."""
    from crew_health.app.camera_reports import MeasurementAssignments, MeasurementReadiness
    from crew_health.camera.application import ports as camera_ports
    from crew_health.kernel.tasks import TaskQueue, TaskRunner
    from crew_health.measurement.application import ports as measurement_ports
    from crew_health.platform.application.tasks import TaskService

    def tasks(resolver: object) -> object:
        return resolver.require(TaskService)  # type: ignore[attr-defined]

    def readiness(resolver: object) -> object:
        return MeasurementReadiness(resolver.require(camera_ports.AdapterReadinessPort))  # type: ignore[attr-defined]

    def assignments(resolver: object) -> object:
        return MeasurementAssignments(resolver.require(camera_ports.AdapterAssignmentPort))  # type: ignore[attr-defined]

    bindings: tuple[tuple[object, Callable[[object], object], tuple[object, ...]], ...] = (
        (TaskQueue, tasks, (TaskService,)),
        (TaskRunner, tasks, (TaskService,)),
        (measurement_ports.AdapterReadinessPort, readiness, (camera_ports.AdapterReadinessPort,)),
        (measurement_ports.AdapterAssignmentPort, assignments, (camera_ports.AdapterAssignmentPort,)),
    )
    for port, factory, requires in bindings:
        if port not in provided:
            registry.provide(port, factory, requires=requires)  # type: ignore[arg-type]
    if reports is not None and camera_ports.AttemptReports not in provided:
        registry.provide(camera_ports.AttemptReports, partial(_constant, reports))  # type: ignore[type-abstract,arg-type]


def _episode_rules(registry: ModuleRegistry, resources: HubResources) -> None:
    """E15's episode-rules.json under E15's real port (episodes/api/CONTRACT.md: the host constructs
    EpisodeRulesSource from validated bytes). E01 has no ConfigName for it yet, so it is parsed once at startup from
    the hub's configuration resources and pinned to the configuration release active at bind time; a document that
    does not parse stops the hub (AGENTS 3, rule 3). Without the file E15 stays unbound, as before."""
    from crew_health.episodes.application.rules import EpisodeRulesSource, parse_rules
    from crew_health.kernel.uow import ReadSnapshotFactory

    path = resources.config / "episode-rules.json"
    if not path.is_file():
        return
    rules = parse_rules(path.read_bytes())

    def source(resolver: object) -> EpisodeRulesSource:
        snapshots = resolver.require(ReadSnapshotFactory)  # type: ignore[attr-defined]
        with snapshots.snapshot() as view:
            return EpisodeRulesSource(rules, view.release.release_id)

    registry.provide(EpisodeRulesSource, source, requires=(ReadSnapshotFactory,))


def production(
    resources: HubResources,
    *,
    origin: Origin,
    demo: DemoOperatorRecord | None = None,
    mission: DemoMission | None = None,
    model: ModelInstallation | None = None,
    stub: StubInstallation | None = None,
    reports: HubReports | None = None,
) -> Callable[[ModuleRegistry], None]:
    """The `configure` hook of a served hub: the offline artifacts and HTTP metadata from the resources, closed
    event sessions and fail-closed stand-ins for every pending port no installed module provides."""
    from crew_health.app import privacy, trends_inputs
    from crew_health.app.assembly import ConfigHttpMetadata
    from crew_health.identity.application.service import IdentityService

    artifacts = config_artifacts(resources)
    metadata = StationMetadata(basis_summaries(resources), origin)
    conditions = PackageConditions.load(resources)

    def e02_metadata(identity: IdentityService) -> ConfigHttpMetadata:
        return ConfigHttpMetadata(metadata.basis_summaries, metadata.origin_of, privacy.visibility(identity))

    def configure(registry: ModuleRegistry) -> None:
        provided = getattr(registry, "providers", {})
        registry.provide(ConfigArtifacts, partial(_constant, artifacts))  # type: ignore[arg-type]
        # E17: a node with installed model artifacts names them; a demo or eval profile may name a pinned stub script.
        if model is not None:
            registry.provide(ModelInstallation, partial(_constant, model))  # type: ignore[arg-type]
        if stub is not None:
            registry.provide(StubInstallation, partial(_constant, stub))  # type: ignore[arg-type]
        _episode_rules(registry, resources)
        if mission is not None:
            _demo_mission(registry, mission)
            provided = getattr(registry, "providers", {})
        # E02 sessions, consent and their consumer adapters first (app/privacy.py); stand-ins fill the rest.
        privacy.compose(
            registry, contract=resources.contracts / "api/openapi.yaml", metadata=e02_metadata, demo=demo
        )
        # E14's inputs: records' accepted observations and E23's accepted sensor series (app/trends_inputs.py).
        trends_inputs.compose(registry)
        if ConfigHttpMetadata not in provided:
            registry.provide(
                ConfigHttpMetadata,
                partial(  # type: ignore[arg-type]
                    _constant,
                    ConfigHttpMetadata(
                        metadata.basis_summaries,
                        metadata.origin_of,
                        cast(ResultVisibility, Unavailable(cast(type[object], ResultVisibility))),
                    ),
                ),
            )
        if SessionAuthorizer not in provided:
            registry.provide(SessionAuthorizer, partial(_constant, ClosedSessions()))  # type: ignore[type-abstract,arg-type]
        registry.provide(ConditionRegistryPort, partial(_constant, conditions))  # type: ignore[type-abstract,arg-type]
        if KitMission not in provided:
            registry.provide(KitMission, partial(_constant, StationMission(origin)))  # type: ignore[type-abstract,arg-type]
        if MissionStatePort not in provided:
            registry.provide(MissionStatePort, partial(_constant, NoMissionFacts()))  # type: ignore[type-abstract,arg-type]
        for port in PENDING_PORTS:
            if port not in provided:
                registry.provide(port, partial(_constant, Unavailable(port)))
        _camera_f1(registry, getattr(registry, "providers", {}), reports)

    return configure
