"""The E12 scenario stack: the production Gateway, SessionAuthority, handlers and RetrievalService, compiled
over E17's own v1 tool catalog (model/tool-catalog/v1/tools.json). Owners that are not on this branch are test
doubles of their canonical ports (E02 IdentityPorts and consent's ObservationPartitions, E05 CapabilityQueries, E10
EvidenceQueryPort), the same doubles hub/tests/tools uses. No clinical content is invented here."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from crew_health.contracts_gen.enums import ConfigName
from crew_health.identity.application.ports import IdentityPorts, RosterEntry
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.testing import MemoryClock, MemoryClocks, MemoryIds, MemorySettings
from crew_health.kernel.uow import SessionContext
from crew_health.measurement.application.ports import CapabilityStatus, CapabilityView, UnavailableReason
from crew_health.tools.adapters.authority import SessionAuthority
from crew_health.tools.adapters.handlers.capabilities import CapabilitiesHandler
from crew_health.tools.adapters.handlers.evidence import ReadPassageHandler, SearchEvidenceHandler
from crew_health.tools.adapters.handlers.proposals import ProposeMeasurementHandler
from crew_health.tools.adapters.jsonschema_validator import CatalogValidator, PolicyValidator
from crew_health.tools.adapters.registry import StaticToolRegistry
from crew_health.tools.application.gateway import Gateway
from crew_health.tools.application.retrieval_service import RetrievalService
from crew_health.tools.domain.arguments import JsonBounds
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.settings import setting_specs
from tests.tools.test_bounds import settings_values
from tests.tools.test_gateway import View

ROOT = Path(__file__).resolve().parents[3]
MODEL = "model_a"
SUBJECT, CREWMATE = "person_a", "person_b"
ROSTER = (RosterEntry(SUBJECT, "Ana", 0, 1, 1), RosterEntry(CREWMATE, "Ben", 1, 1, None))


def settings() -> MemorySettings:
    return MemorySettings(setting_specs(), settings_values())


@dataclass
class Roster:
    entries: tuple[RosterEntry, ...] = ROSTER

    def members(self, view: object, *, after_index: int | None, limit: int) -> tuple[RosterEntry, ...]:
        return self.entries[:limit]

    def member(self, view: object, person_id: str) -> RosterEntry | None:
        return next((entry for entry in self.entries if entry.person_id == person_id), None)


@dataclass
class Sessions:
    """E02's SessionReader: a revoked session is no longer alive, mid-turn included."""

    live: bool = True

    def alive(self, snapshot: object, session: SessionContext) -> bool:
        return self.live

    def access_generation(self, view: object) -> int:
        return 1


@dataclass(frozen=True)
class Authored:
    patient_id: str
    operator_id: str


@dataclass
class Records:
    """consent's ObservationPartitions: observation id to the patient it belongs to."""

    rows: Mapping[str, Authored] = field(default_factory=dict)

    def observation(self, view: object, observation_id: str) -> Authored | None:
        return self.rows.get(observation_id)


def capability(code: str, status: CapabilityStatus, reason: UnavailableReason | None) -> CapabilityView:
    return CapabilityView(
        code,
        code,
        ("manual_instrument",),
        (),
        (),
        (),
        "self",
        (),
        status,
        reason,
        None,
        "heart",
        ("hr",),
    )


@dataclass
class Capabilities:
    """E05's CapabilityQueries registry view."""

    items: tuple[CapabilityView, ...] = ()

    def get(self, uow: object, code: str) -> CapabilityView | None:
        return next((item for item in self.items if item.code == code), None)

    def list(self, uow: object) -> tuple[CapabilityView, ...]:
        return self.items


@dataclass
class Stack:
    gateway: Gateway
    sessions: Sessions
    records: Records
    capabilities: Capabilities


def stack(retrieval: RetrievalService | None = None, *, identity: bool = True) -> Stack:
    hub = settings()
    sessions, records, capabilities = Sessions(), Records(), Capabilities()
    ports = IdentityPorts(Roster(), None, sessions, None, None)  # type: ignore[arg-type]
    authority = SessionAuthority(
        lambda: ports if identity else None, lambda: records, max_members=16, max_strings=256
    )
    registry = StaticToolRegistry(hub)
    registry.register("get_capabilities", CapabilitiesHandler(capabilities, hub))
    registry.register("read_source_passage", ReadPassageHandler(lambda scope: retrieval, hub))
    registry.register("search_evidence", SearchEvidenceHandler(lambda scope: retrieval))
    registry.register("propose_measurement", ProposeMeasurementHandler(lambda: capabilities))
    registry.freeze()
    document = json.loads((ROOT / "model/tool-catalog/v1/tools.json").read_text(encoding="utf-8"))
    catalog = CatalogValidator(hub).compile_release(
        tuple(document["tools"]),
        {phase: tuple(names) for phase, names in document["exposure"].items()},
        model_release_id=MODEL,
    )
    parsed = PolicyValidator(JsonBounds(262144, 16, 50, 2000)).parse(
        View().release.document(ConfigName.ASSISTANT_POLICY).raw
    )
    clock = MemoryClock(View.now, 0, "epoch_a")
    gate = Gateway(
        registry,
        lambda model_release_id: catalog if model_release_id == MODEL else None,
        hub,
        authority,
        clock,
        MemoryIds(identifiers=tuple(str(i) for i in range(200))),
        policy=lambda uow: parsed,
        corrections=MemoryClocks(
            settings=View().factory.settings, clock=clock, node_id="node_a", incarnation="inc_a"
        ),
        capture=lambda uow, anchor: TimeInput(
            clock.now(), anchor.source_clock_id, anchor.segment_id, 1, "hub_corrected", "LIVE"
        ),
    )
    return Stack(gate, sessions, records, capabilities)


def audits(view: View) -> str:
    return str(view.audits)


__all__ = ["CREWMATE", "MODEL", "SUBJECT", "Stack", "ToolScope", "capability", "stack"]
