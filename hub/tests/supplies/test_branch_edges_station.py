"""Edge cases of the supplies use cases and provided ports on E01's real SQLite station: bounded ledger reads, fail
closed references, step uses against a pinned release or a corrupt ledger, the what-if, refusal evidence and the
activation guard (E32 3.3, 4.1, 4.2, 5.1; HZ14)."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.help.application.ports import HelpContext
from crew_health.kernel.commands import AcceptedReceipt, StoredResult
from crew_health.kernel.config import CandidateRelease, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, ReadSnapshot
from crew_health.supplies.adapters.sqlite_ledger import SqliteLedger
from crew_health.supplies.application import provided, use_cases
from crew_health.supplies.application.context import Deps, read_heads
from crew_health.supplies.application.events import publish_refusal
from crew_health.supplies.application.ports import (
    RunContext,
    StockLevel,
    SupplyNeed,
    SupplyPartition,
)
from crew_health.supplies.application.references import read_reference
from crew_health.supplies.application.release_index import SuppliesActivationGuard
from crew_health.supplies.application.repository import StoredRefusal, View
from crew_health.supplies.application.step_uses import record_step_uses
from crew_health.supplies.domain.errors import ReferenceNotVisible, UnitMismatch, UseInvalid, WhatIfInvalid
from crew_health.supplies.domain.model import Head, Partition
from crew_health.supplies.module import build_deps

from .harness import (
    NOW,
    Station,
    build_station,
    crew_session,
    document,
    example,
    headers,
    limits,
    release,
    use_body,
)

RINSE = Unit("{rinse}")
AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
ACTOR = Actor("person", "person_a")
LUNAR = Partition("mission_1", "SIM", "lunar_surface", "lunar_surface")


@pytest.fixture
def station(tmp_path: Path) -> Iterator[Station]:
    built = build_station(tmp_path)
    built.sessions.by_token["crew"] = crew_session()
    yield built


@pytest.fixture
def deps(station: Station) -> Deps:
    """A second wiring of the module over the same station, so a test can swap one dependency."""
    return build_deps(station.resolver)


def count(station: Station, table: str) -> int:
    value = station.rows(f"SELECT count(*) FROM {table}")[0][0]
    assert isinstance(value, int)
    return value


def refusal_audits(station: Station) -> int:
    return sum(
        1
        for (envelope,) in station.rows("SELECT envelope FROM platform_audit")
        if isinstance(envelope, bytes) and b"supplies.use_refused" in envelope
    )


def with_limits(deps: Deps, *, max_supplies: int, page_rows: int | None = None) -> Deps:
    rows = deps.limits.page_rows if page_rows is None else page_rows
    return replace(deps, limits=replace(deps.limits, max_supplies=max_supplies, page_rows=rows))


# ---- bounded ledger reads ----


@pytest.mark.trace("AGENTS 3.2", "supplies.max_supplies")
def test_more_used_supplies_than_the_bound_are_refused_never_truncated(station: Station, deps: Deps) -> None:
    for key, supply in (("use-key-a", "eye_rinse_water"), ("use-key-b", "towels")):
        response = station.client.post(
            f"/v1/supplies/{supply}/uses", headers=headers(key=key), json=use_body(1)
        )
        assert response.status_code == 201, response.text
    with station.db.snapshot() as view:
        assert set(read_heads(with_limits(deps, page_rows=1, max_supplies=2), view, LUNAR)) == {
            "eye_rinse_water",
            "towels",
        }
        with pytest.raises(ProblemError) as caught:
            read_heads(with_limits(deps, page_rows=1, max_supplies=1), view, LUNAR)
    assert caught.value.code == ErrorCode.PAYLOAD_TOO_LARGE


class _StuckKeyset(SqliteLedger):
    """A storage adapter whose keyset never advances: every page is the same full page."""

    def heads(
        self, view: View, partition: Partition, *, after_supply_id: str, limit: int
    ) -> Mapping[str, Head]:
        return {f"supply_{i}": Head(1, Quantity(Decimal(1), RINSE), None, None) for i in range(limit)}


@pytest.mark.trace("AGENTS 3.2", "rule 5")
def test_a_ledger_read_that_never_ends_is_refused_after_its_page_bound(station: Station, deps: Deps) -> None:
    stuck = replace(with_limits(deps, page_rows=2, max_supplies=4), repository=_StuckKeyset())
    with station.db.snapshot() as view, pytest.raises(ProblemError) as caught:
        read_heads(stuck, view, LUNAR)
    assert caught.value.code == ErrorCode.PAYLOAD_TOO_LARGE


# ---- references: fail closed ----


@pytest.mark.trace("E32 3.3 use admission", "fail closed", "rule 3")
def test_a_reference_whose_owner_is_absent_or_does_not_show_it_is_never_admitted(
    station: Station, deps: Deps
) -> None:
    session = crew_session()
    with pytest.raises(ReferenceNotVisible):  # no plan run owner wired
        read_reference(replace(deps, plan_runs=None), session, "run_1", None)
    with pytest.raises(ReferenceNotVisible):  # no help owner wired
        read_reference(replace(deps, help_sessions=None), session, None, "help_1")
    with pytest.raises(ReferenceNotVisible):  # the plan run is unknown to its owner
        read_reference(deps, session, "run_1", None)
    station.runs.contexts["run_1"] = RunContext(False, "plan_eye_injury", "lunar_surface", "SIM", {})
    with pytest.raises(ReferenceNotVisible):  # known, but not visible to this session
        read_reference(deps, session, "run_1", None)
    station.help.contexts["help_1"] = HelpContext(True, "SIM", "lunar_surface")
    assert read_reference(deps, session, None, "help_1").kind == "help_session"


# ---- step uses ----


@dataclass
class _Releases:
    """The configuration reader a pinned run's release comes from."""

    pinned: MemoryRelease

    def active(self) -> ReleaseHandle:
        return self.pinned

    def release(self, release_id: str) -> ReleaseHandle:
        if release_id != self.pinned.release_id:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "unknown release")
        return self.pinned


def pinned_release(change: Callable[[dict[str, object]], None]) -> MemoryRelease:
    kit = copy.deepcopy(example("kit-map.example.json"))
    assert isinstance(kit, dict)
    change(kit)
    return release("release_0", {ConfigName.KIT_MAP: kit})


def _supply(kit: dict[str, object], supply_id: str) -> dict[str, object]:
    supplies = kit["supplies"]
    assert isinstance(supplies, list)
    found = next(s for s in supplies if isinstance(s, dict) and s["supply_id"] == supply_id)
    assert isinstance(found, dict)
    return found


def step(
    deps: Deps, station: Station, uses: Sequence[SupplyNeed], pinned: str = "release_0"
) -> tuple[str, ...]:
    with station.db.begin(actor=ACTOR, correlation_id="cor-step") as uow:
        return tuple(
            record_step_uses(
                deps,
                uow,
                environment_id="lunar_surface",
                plan_run_id="run_1",
                step_id="eye_rinse",
                occurrence=1,
                pinned_release_id=pinned,
                uses=uses,
                actor=ACTOR,
                at=AT,
            )
        )


def need(supply_id: str, per_use: int, unit: str) -> SupplyNeed:
    return SupplyNeed("plan_eye_injury", "eye_rinse", supply_id, Quantity(Decimal(per_use), Unit(unit)))


@pytest.mark.trace("INV-S14", "E32 4.2")
def test_a_step_pinned_to_a_release_this_hub_cannot_read_fails_loud(station: Station, deps: Deps) -> None:
    with pytest.raises(ProblemError) as caught:
        step(replace(deps, releases=None), station, [need("eye_rinse_water", 1, "rinse")])
    assert caught.value.code == ErrorCode.INTERNAL_ERROR and caught.value.retryable is False
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("INV-S14", "E32 4.2", "open work stays pinned")
def test_consumable_comes_from_the_runs_pinned_release(station: Station, deps: Deps) -> None:
    kept = pinned_release(lambda kit: _supply(kit, "towels").update(consumable=False))
    pinned = replace(deps, releases=_Releases(kept))
    ids = step(pinned, station, [need("eye_rinse_water", 1, "rinse"), need("towels", 2, "towel")])
    assert len(ids) == 1  # the pinned plan keeps its towels, so only the rinse water is debited
    assert station.rows("SELECT supply_id FROM supplies_use") == [("eye_rinse_water",)]


@pytest.mark.trace("INV-S14", "S-A1", "rule 8")
def test_a_supply_whose_unit_changed_since_the_pin_is_refused_and_nothing_is_written(
    station: Station, deps: Deps
) -> None:
    drops = pinned_release(lambda kit: _supply(kit, "eye_rinse_water").update(unit="drop"))
    with pytest.raises(UnitMismatch) as caught:
        step(replace(deps, releases=_Releases(drops)), station, [need("eye_rinse_water", 1, "drop")])
    assert (caught.value.left, caught.value.right) == ("{drop}", "{rinse}")
    assert count(station, "supplies_use") == 0


@pytest.mark.trace("E32 5.1", "INV-S12")
def test_a_step_use_on_a_ledger_that_failed_verification_is_refused(station: Station, deps: Deps) -> None:
    deps.corrupt.mark(frozenset({(LUNAR, "eye_rinse_water")}))
    with pytest.raises(ProblemError) as caught:
        step(deps, station, [need("eye_rinse_water", 1, "rinse")], pinned="release_1")
    assert caught.value.code == ErrorCode.INTERNAL_ERROR and caught.value.retryable is False
    assert count(station, "supplies_use") == 0


# ---- provided ports ----


@pytest.mark.trace("SYSTEM 8.1", "E24 rows")
def test_cover_facts_with_no_room_return_no_rows(station: Station, deps: Deps) -> None:
    where = SupplyPartition("mission_1", "lunar_surface", "lunar_surface", "SIM")
    with station.db.snapshot() as view:
        assert provided.cover_facts(deps, view, where, None, 0) == ()
        assert len(provided.cover_facts(deps, view, where, None, 1)) == 1


@pytest.mark.trace("R28-20", "CONTRACTS 22 rule 6", "SYSTEM 8.1")
def test_uses_after_one_is_null_when_the_quantity_or_every_per_use_is_not_set(deps: Deps) -> None:
    unset = {"eye_rinse_water": StockLevel("eye_rinse_water", True, None, RINSE)}
    six = {"eye_rinse_water": StockLevel("eye_rinse_water", True, Quantity(Decimal(6), RINSE), RINSE)}
    rinse = need("eye_rinse_water", 1, "rinse")
    no_amount = SupplyNeed("plan_eye_injury", "eye_rinse", "eye_rinse_water", None)
    key = ("eye_rinse", "eye_rinse_water")
    assert provided.uses_after_one(deps, "lunar_surface", [rinse], unset) == {key: None}
    assert provided.uses_after_one(deps, "lunar_surface", [no_amount], six) == {key: None}
    assert provided.uses_after_one(deps, "lunar_surface", [rinse], six) == {key: 5}


@dataclass(frozen=True)
class _ReleaseRef:
    release_id: str


@dataclass(frozen=True)
class _Context:
    snapshot: ReadSnapshot
    release: _ReleaseRef
    environment_id: str
    at: datetime


@dataclass(frozen=True)
class _Addition:
    supply_id: str
    quantity: Quantity


SHIELDS = _Addition("eye_shield", Quantity(Decimal(2), Unit("shield")))


@pytest.mark.trace("INV-S11", "E32 5.1", "E32-T-21")
def test_a_what_if_on_a_corrupt_partition_answers_unknown_cover(station: Station, deps: Deps) -> None:
    leo = Partition("mission_1", "SIM", "station_leo", "station_leo")
    with station.db.snapshot() as view:
        context = _Context(view, _ReleaseRef("release_1"), "station_leo", AT)
        assert [(c.from_uses, c.to_uses) for c in provided.cover_changed(deps, context, [SHIELDS])] == [
            (None, 2)
        ]
        deps.corrupt.mark(frozenset({(leo, "eye_shield")}))
        assert [(c.from_uses, c.to_uses) for c in provided.cover_changed(deps, context, [SHIELDS])] == [
            (None, None)
        ]


@pytest.mark.trace("INV-S11", "rule 8", "supplies.max_supplies")
def test_a_what_if_refuses_another_unit_and_more_additions_than_the_bound(
    station: Station, deps: Deps
) -> None:
    towels = _Addition("towels", Quantity(Decimal(1), Unit("towel")))
    with station.db.snapshot() as view:
        context = _Context(view, _ReleaseRef("release_1"), "station_leo", AT)
        with pytest.raises(WhatIfInvalid) as unit:
            provided.cover_changed(
                deps, context, [_Addition("eye_shield", Quantity(Decimal(2), Unit("towel")))]
            )
        with pytest.raises(WhatIfInvalid) as bound:
            provided.cover_changed(with_limits(deps, max_supplies=1), context, [SHIELDS, towels])
    assert [i.field for i in unit.value.issues] == ["/add_supplies/0/quantity"]
    assert [i.field for i in bound.value.issues] == ["/add_supplies"]


# ---- RecordSupplyUse: validation, replay and refusal evidence ----


def use_request(
    quantity: Decimal = Decimal(9),
    *,
    supply_id: str = "eye_rinse_water",
    help_session_id: str | None = "help_1",
    client_time: datetime = AT,
) -> use_cases.UseRequest:
    return use_cases.UseRequest(
        supply_id=supply_id,
        quantity=quantity,
        plan_run_id=None,
        help_session_id=help_session_id,
        step_id="eye_rinse",
        client_time=client_time,
        key="offline-use-1",
        config_release=None,
        body_sha256=hashlib.sha256(b"offline use body").hexdigest(),
        correlation_id="cor-evidence",
    )


@pytest.mark.trace("E32 6.1", "rule 5")
@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")])
def test_a_use_quantity_not_a_finite_amount_above_zero_is_refused(deps: Deps, quantity: Decimal) -> None:
    with pytest.raises(UseInvalid) as caught:
        use_cases.validate(deps, use_request(quantity))
    assert [i.field for i in caught.value.issues] == ["/quantity"]


@pytest.mark.trace("E32 6.1", "D3")
def test_a_use_whose_client_time_has_no_offset_is_refused(deps: Deps) -> None:
    with pytest.raises(UseInvalid) as caught:
        use_cases.validate(deps, use_request(client_time=datetime(2026, 11, 14, 12, 0)))
    assert [i.field for i in caught.value.issues] == ["/client_time"]


@pytest.mark.trace("rule 6", "E32 4.1")
def test_a_replay_whose_stored_use_is_missing_fails_loud_never_answers_empty(
    station: Station, deps: Deps
) -> None:
    session, request = crew_session(), use_request()
    identity = use_cases._identity(session, request, "mission_1", "SIM")
    receipt = AcceptedReceipt(identity.key, identity.operation, identity.target, "receipt_1", NOW)
    with pytest.raises(ProblemError) as caught:
        with station.db.begin(actor=session.actor, correlation_id="cor-replay") as uow:
            use_cases._replay(
                deps, uow, identity, StoredResult(receipt, 201, "sup_missing", "accepted", None)
            )
    assert caught.value.code == ErrorCode.INTERNAL_ERROR and caught.value.retryable is False


def evidence(deps: Deps, request: use_cases.UseRequest) -> None:
    identity = use_cases._identity(crew_session(), request, "mission_1", "SIM")
    use_cases._refusal_evidence(deps, crew_session(), identity, request, ErrorCode.SUPPLY_BELOW_ZERO)


def recorded(station: Station, deps: Deps, request: use_cases.UseRequest) -> StoredResult | None:
    identity = use_cases._identity(crew_session(), request, "mission_1", "SIM")
    with station.db.begin(actor=ACTOR, correlation_id="cor-lookup") as uow:
        return deps.idempotency.lookup(uow, identity)


@pytest.mark.trace("HZ14", "E32-T-42", "rule 6")
def test_refusal_evidence_is_written_once_per_key(station: Station, deps: Deps) -> None:
    evidence(deps, use_request())
    evidence(deps, use_request())
    assert count(station, "supplies_use_refused") == 1 and refusal_audits(station) == 1
    stored = recorded(station, deps, use_request())
    assert stored is not None and (stored.status, stored.result_code) == (422, "SUPPLY_BELOW_ZERO")


@pytest.mark.trace("HZ14", "fail closed")
@pytest.mark.parametrize(
    "request_",
    [use_request(supply_id="oxygen_mask"), use_request(help_session_id=None)],
    ids=["unlisted", "no_session"],
)
def test_no_evidence_is_kept_for_an_unlisted_supply_or_a_use_without_a_help_session(
    station: Station, deps: Deps, request_: use_cases.UseRequest
) -> None:
    evidence(deps, request_)
    assert count(station, "supplies_use_refused") == 0 and refusal_audits(station) == 0
    assert recorded(station, deps, request_) is None


@pytest.mark.trace("HZ14", "rule 6")
def test_evidence_that_outlived_its_command_record_is_never_written_twice(
    station: Station, deps: Deps
) -> None:
    request = use_request()
    identity = use_cases._identity(crew_session(), request, "mission_1", "SIM")
    earlier = StoredRefusal(
        refusal_id="supr_earlier",
        partition=LUNAR,
        supply_id="eye_rinse_water",
        quantity=Quantity(request.quantity, RINSE),
        help_session_id="help_1",
        step_id="eye_rinse",
        code=ErrorCode.SUPPLY_BELOW_ZERO.value,
        command_key=use_cases._command_key(identity),
        at=NOW,
        observed_at=AT,
    )
    with station.db.begin(actor=ACTOR, correlation_id="cor-earlier") as uow:
        assert deps.repository.append_refusal(uow, earlier)
        publish_refusal(uow, deps.ids, earlier, uow.release.release_id)
    evidence(deps, request)
    assert station.rows("SELECT refusal_id FROM supplies_use_refused") == [("supr_earlier",)]
    assert refusal_audits(station) == 1
    stored = recorded(station, deps, request)
    assert stored is not None and stored.status == 422  # the retry now replays the refusal


# ---- activation guard ----


@pytest.mark.trace("S-A1", "E32-T-17", "corrupt or missing configuration")
def test_the_guard_skips_a_candidate_without_a_kit_map_and_reports_an_unreadable_one(
    station: Station,
) -> None:
    guard = SuppliesActivationGuard(lambda: SqliteLedger(), lambda: station.mission, limits)
    kit = copy.deepcopy(example("kit-map.example.json"))
    assert isinstance(kit, dict)
    _supply(kit, "towels")["unit"] = "two words"
    copy_only = {ConfigName.UI_COPY: document(ConfigName.UI_COPY, example("config/ui-copy.example.json"))}
    broken = {ConfigName.KIT_MAP: document(ConfigName.KIT_MAP, kit)}
    with station.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor-guard") as uow:
        assert guard.check(uow, CandidateRelease("release_9", copy_only)) == ()
        findings = guard.check(uow, CandidateRelease("release_9", broken))
    # The unreadable unit drops the supply, so its stock rows name an unlisted supply: the guard reports the
    # document's own findings and never reads the ledger for a kit map it cannot read.
    assert (findings[0].check_id, findings[0].pointer) == ("supplies.release.S-L7", "/supplies/2/unit")
    assert {f.check_id for f in findings} == {"supplies.release.S-L7", "supplies.release.S-L1"}
