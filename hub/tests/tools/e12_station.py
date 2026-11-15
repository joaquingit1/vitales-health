"""E12 on the real composed hub: E01's create_app with every installed module (hub/tests/app/test_composition.py's
station over the station resources of hub/tests/app/conftest.py), signed in through E02's identity service.

The gateway, its audit, scope authority, catalog compilation, policy pinning and clock seam are production code
resolved by port class from the running app. Seams whose owner is not on main are supplied through the composer as
test doubles of the owners' canonical ports, never as hub service replacements:
- E10's EvidenceQueryPort (wave 2 canonical ports, 728c2b7) and E10's ContentFacts answer for one pinned release;
- E17's real ModelReleaseReader reads a release installed in the test's data directory (E17's own candidate manifest);
  E17 provides no TokenCounter port yet (docs/requests/E17.md), so a double counts one token per byte;
- E23's EnvironmentFacts answers for content naming no environment.
Every double's data is built here from the package; no clinical content is invented.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.guidance.application.ports import (
    ContentFacts,
    ContentFactSet,
    ContentRef,
    EnvironmentFacts,
    Fact,
    Known,
)
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import OperatorProof
from crew_health.identity.application.service import IdentityService
from crew_health.identity.domain.roster import ProvisionedPrincipal, RosterBootstrap, RosterMember
from crew_health.identity.domain.session import SignInRequest
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock, MissionClockVersion
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWorkFactory
from crew_health.knowledge.application.dto import AllowedUse
from crew_health.knowledge.application.ports import (  # type: ignore[attr-defined]
    EvidenceQueryPort,
    dto,
)
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.application.ports import ModelInstallation
from crew_health.model.application.ports import TokenCounter as ModelTokenCounter
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope
from tests.app.test_composition import Station, station  # type: ignore[import-not-found]

MODEL_RELEASE = "model_candidate"  # the release id of hub/tests/model/conftest.py's manifest
EVIDENCE_RELEASE = "evr_sim_a"
USE: AllowedUse = "risk_context"
OPERATOR = OperatorProof(Actor("operator", "operator_sim"), None)
MISSION = "mission_e12_sim"
CREW = (
    RosterMember("person_sim", "Ana", 0, 1, None, 1),
    RosterMember("person_other", "Ben", 1, 1, None, None),
)


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

    def read_approved(self, release_id: str, passage_id: str, use: AllowedUse) -> dto.ApprovedRead:
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
        status = None if row is None else dict[str, str](row.use_status).get(purpose)
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


def install_model(store: ManifestStore, *, refused_catalog: bool = False) -> ModelInstallation:
    """E17's real installation: the candidate release of hub/tests/model/conftest.py made active in its data
    directory, with the repository's v1 catalog. With refused_catalog, the release's catalog also lists a tool
    whose arguments carry a patient_id, which E17's own catalog check (ManifestStore.tool_catalog) refuses."""
    if refused_catalog:
        folder = store.root / "tool-catalog/v1"
        document = json.loads((folder / "tools.json").read_text(encoding="utf-8"))
        schema = {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "additionalProperties": False,
        }
        document["tools"].append(
            {"name": "read_chart", "stage": "intake", "effect": "read", "input_schema": schema}
        )
        raw = json.dumps(document, indent=2).encode("utf-8")
        (folder / "tools.json").write_bytes(raw)
        digest = {"tools.json": hashlib.sha256(raw).hexdigest()}
        (folder / "MANIFEST.sha256").write_text(json.dumps(digest), encoding="utf-8")
    pointer = store.data_dir / "models/active.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"release_id": MODEL_RELEASE}), encoding="utf-8")
    return ModelInstallation(str(store.root), str(store.data_dir), "eval", "/opt/model/llama-server", 18081)


class Tokens:
    """E17's TokenCounter double: one token per UTF-8 byte, never fewer than the real tokenizer would count."""

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))

    def count_request(self, body: Mapping[str, object]) -> int:
        return len(canonical_json(body))


@dataclass
class E12Hub:
    station: Station
    base: Callable[[ModuleRegistry], None]
    evidence: Evidence
    installation: ModelInstallation | None
    tokens: Tokens | None

    def configure(self, registry: ModuleRegistry) -> None:
        self.base(registry)
        evidence, installation, tokens = self.evidence, self.installation, self.tokens
        registry.provide(EvidenceQueryPort, _constant(evidence))  # type: ignore[type-abstract]
        registry.provide(ContentFacts, _constant(Content(evidence)))  # type: ignore[type-abstract]
        registry.provide(EnvironmentFacts, _constant(Places()))  # type: ignore[type-abstract]
        if installation is not None:
            registry.provide(ModelInstallation, _constant(installation))
        if tokens is not None:
            registry.provide(ModelTokenCounter, _constant(tokens))  # type: ignore[type-abstract]


def _constant[T](value: T) -> Callable[[Resolver], T]:
    def provide(resolver: Resolver) -> T:
        return value

    return provide


def e12_hub(
    tmp_path: Path,
    resources: Path,
    *,
    evidence: Evidence | None = None,
    installation: ModelInstallation | None = None,
    tokens: Tokens | None = None,
) -> E12Hub:
    """The composed hub; without an installation E17's real reader has no active release."""
    base = station(tmp_path, resources)
    hub = E12Hub(
        base, base.configure, evidence or Evidence(member("psg_sim_a")), installation, tokens or Tokens()
    )
    base.configure = hub.configure
    return hub


def credential(person_id: str) -> str:
    return "sim passphrase for " + person_id


def sign_in(resolver: object, person_id: str) -> SessionContext:
    """E02's own operator path: the first mission clock version, the roster document, a credential, then the crew
    member's sign-in. Without a mission clock version no roster member is current (R-E02-32), so sign-in refuses;
    a served hub appends it at its first start (app.cli confirm_time), this harness does it here."""
    clock = resolver.require(MissionClock)  # type: ignore[attr-defined]
    uows: UnitOfWorkFactory = resolver.require(UnitOfWorkFactory)  # type: ignore[attr-defined]
    ids: IdFactory = resolver.require(IdFactory)  # type: ignore[attr-defined]
    with uows.begin(actor=OPERATOR.actor, correlation_id="sim_mission_clock") as uow:
        if not uow.connection.rows("SELECT clock_version FROM clock_mission_version", (), limit=1):
            clock.append(uow, MissionClockVersion("mcv_sim", 1, uow.now, uow.now, None, "Day {day}"), ids=ids)
    admin: IdentityAdmin = resolver.require(IdentityAdmin)  # type: ignore[attr-defined]
    identity: IdentityService = resolver.require(IdentityService)  # type: ignore[attr-defined]
    roster = RosterBootstrap(
        MISSION,
        "SIM",
        CREW,
        (
            ProvisionedPrincipal("stn_sim", "station", "Station"),
            ProvisionedPrincipal("operator_sim", "operator", "Operator"),
        ),
    )
    try:
        admin.apply_roster(roster, expected_revision=0, operator=OPERATOR, correlation_id="sim_roster")
    except ProblemError as error:
        if error.code != ErrorCode.STALE_VERSION:
            raise
    admin.set_credential(
        person_id,
        credential(person_id),
        current_credential=None,
        operator=OPERATOR,
        client="console",
        correlation_id="sim_credential_" + person_id,
    )
    view = identity.sign_in(
        SignInRequest("crew", person_id),
        credential(person_id),
        client="console",
        listener="local",
        correlation_id="sim_sign_in_" + person_id,
    )
    assert view.token is not None
    return identity.authenticate(view.token, listener="local")


def tool_scope(
    session: SessionContext,
    anchor: TimeInput,
    *,
    phase: Phase = Phase.ASSESSMENT,
    turn: str = "turn_sim_a",
    release: str,
    seconds: int = 60,
    model: str = MODEL_RELEASE,
) -> ToolScope:
    """The controller's turn scope for the signed-in crew member, anchored on a capture of the hub clock."""
    return ToolScope(
        session,
        session.person_id,
        MISSION,
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
