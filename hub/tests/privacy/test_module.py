"""E02 registration (S5.12) and its fail-closed edges: declarations, audiences, late binding, the vault."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import pytest
from e02_station import light

from crew_health.app.registry import DeclarationRegistry, Resolver, StartupPhase
from crew_health.consent import module as consent_module
from crew_health.consent.application.ports import ConsentPorts, ObservationPartitions
from crew_health.consent.application.records import ConsentRecordAuthorization
from crew_health.consent.application.settings import specs as consent_specs
from crew_health.identity import module as identity_module
from crew_health.identity.adapters.vault import UnavailableVault
from crew_health.identity.api.router import IdentityRuntime, IdentitySecurity
from crew_health.identity.application.ports import IdentityPorts
from crew_health.identity.application.settings import specs as identity_specs
from crew_health.identity.domain.session import roles_for
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import SealedBlob, SealedPrivateRef
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    Role,
    SessionContext,
    UnitOfWorkFactory,
)
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.records.adapters.repository import RecordRepository


class _Provide(Protocol):
    def provide(
        self,
        port: type[object],
        factory: Callable[[Resolver], object],
        *,
        requires: tuple[type[object], ...] = (),
    ) -> None: ...


def declared() -> DeclarationRegistry:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    identity_module.register(registry)
    consent_module.register(registry)
    return registry


def constant(value: object) -> Callable[[Resolver], object]:
    def factory(resolver: Resolver) -> object:
        return value

    return factory


def kernel(registry: DeclarationRegistry, directory: Path) -> None:
    services = light(directory, seed=False)
    db, ledger = services.database, CommandLedger(services.database.ids, settings=services.database.settings)
    values: dict[type[object], object] = {
        UnitOfWorkFactory: db,
        ReadSnapshotFactory: db,
        Clock: db.clock,
        IdFactory: db.ids,
        HubSettings: db.settings,
        IdempotencyStore: ledger,
        CommandReceipts: ledger,
    }
    loose: _Provide = registry
    for port, value in values.items():
        loose.provide(port, constant(value))


@pytest.mark.trace("T-E02-019", "T-E02-020")
def test_both_modules_declare_their_settings_prefixes_audiences_and_hooks() -> None:
    declarations = declared().declarations
    expected = {spec.key for spec in (*identity_specs(), *consent_specs())}
    assert set(declarations["settings"]) == expected
    assert declarations["prefixes"] == {
        "ses": "identity.session",
        "owner": "identity.owner_proof",
        "cns": "consent.grant",
        "fence": "consent.fence",
    }
    assert set(declarations["scheduler_handlers"]) == {"consent.expire"}
    assert set(declarations["subscriptions"]) == {
        "consent.fence_on_correction",
        "consent.fence_on_regression",
    }
    startup = {key for key in declarations["startup"] if isinstance(key, tuple)}
    assert len(startup) == len(declarations["startup"]) == 2
    assert {key[0] for key in startup} == {StartupPhase.STATE_SWEEP}
    for rule in declarations["audiences"].values():
        assert hasattr(rule, "admits")


@pytest.mark.trace("T-E02-001", "T-E02-020")
def test_record_authorization_resolves_without_observation_facts_and_binds_them_late(tmp_path: Path) -> None:
    """Observation facts are optional: a hub without them composes (finding 7) and releases nothing to a
    non-subject; once the composer binds them, the authorization reads them."""
    bare = declared()
    kernel(bare, tmp_path / "bare")
    authorization = bare.resolve().require(ConsentRecordAuthorization)
    assert authorization.bound.observations is None
    complete = declared()
    kernel(complete, tmp_path / "complete")
    loose: _Provide = complete
    loose.provide(ObservationPartitions, lambda resolver: RecordRepository())
    resolver = complete.resolve()
    assert isinstance(resolver.require(ConsentRecordAuthorization).bound.observations, RecordRepository)
    assert isinstance(resolver.require(ConsentPorts), ConsentPorts)
    assert isinstance(resolver.require(IdentityPorts), IdentityPorts)


@pytest.mark.trace("T-E02-020")
def test_both_modules_register_their_audit_policies() -> None:
    policies = declared().declarations["audit_policies"]
    assert set(policies) == {
        "identity.session_started",
        "identity.session_ended",
        "identity.credential_set",
        "identity.roster_applied",
        "consent.granted",
        "consent.ended",
        "consent.fenced",
    }


@pytest.mark.trace("T-E02-001")
def test_reference_audiences_admit_no_session(tmp_path: Path) -> None:
    services = light(tmp_path)
    officer = SessionContext("ses_1", "crew", Actor("person", "ilya"), "ilya", roles_for("crew", 1))
    audience = Audience("local", "cns_1", AudienceProjectionKey("consent", "grant", 1), "e02.consent.1")
    with services.database.snapshot() as view:
        for rule in (identity_module.LocalOnly(), consent_module.LocalOnly()):
            assert not rule.admits(view, officer, audience)


@pytest.mark.trace("T-E02-019", "T-E02-001")
def test_security_refuses_a_context_whose_roles_do_not_follow_from_its_kind() -> None:
    runtime = IdentityRuntime()
    security = IdentitySecurity(runtime)
    coherent = (
        SessionContext("ses_1", "crew", Actor("person", "ilya"), "ilya", roles_for("crew", 1)),
        SessionContext("ses_2", "station", Actor("station", "stn_galley"), None, roles_for("station", None)),
    )
    for session in coherent:
        security.authorize(session, "getCurrentSession")
    incoherent = (
        SessionContext("ses_3", "station", Actor("station", "stn_galley"), None, frozenset({Role.CREW})),
        SessionContext("ses_4", "crew", Actor("person", "mara"), None, roles_for("crew", None)),
        SessionContext(
            "ses_5",
            "adapter",
            Actor("adapter", "cuff"),
            None,
            frozenset({Role.ADAPTER, Role.CREW_MEDICAL_OFFICER}),
        ),
        SessionContext(
            "ses_6", "station", Actor("station", "stn_galley"), "mara", roles_for("station", None)
        ),
    )
    for session in incoherent:
        with pytest.raises(ProblemError) as error:
            security.authorize(session, "getObservation")
        assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T-E02-019", "T-E02-020")
def test_an_unbound_module_fails_loudly_and_retryably() -> None:
    runtime = IdentityRuntime()
    for call in (runtime.service, runtime.admin):
        with pytest.raises(ProblemError) as error:
            call()
        assert (error.value.code, error.value.retryable) == (ErrorCode.INTERNAL_ERROR, True)
    with pytest.raises(ValueError, match="before the module was bound"):
        consent_module.Bound().service()


@pytest.mark.trace("T-E02-014", "R20")
def test_the_vault_location_opens_nothing_until_a_backend_is_named() -> None:
    vault = UnavailableVault()
    session = SessionContext("ses_1", "crew", Actor("person", "mara"), "mara", roles_for("crew", None))
    reference = SealedPrivateRef("jrn_1", "v1", "journal", "mara", "key_1", "f", "k", "0" * 64)
    calls: tuple[Callable[[], object], ...] = (
        lambda: vault.create_entry_key(owner_person_id="mara", entry_id="jrn_1"),
        lambda: vault.revoke(owner_person_id="mara", entry_ids=("jrn_1",), command_key="key_1"),
        lambda: vault.revocation("rev_1"),
        lambda: vault.fingerprint(owner_person_id="mara", canonical_identity_and_body=b"{}"),
        lambda: vault.encrypt(key_id="k", plaintext=b"private", associated_data=b""),
        lambda: vault.decrypt(
            session, reference=reference, blob=SealedBlob(b"n", b"c", b"w"), associated_data=b""
        ),
    )
    for call in calls:
        with pytest.raises(ProblemError) as error:
            call()
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert vault.is_revoked(entry_id="jrn_1")
