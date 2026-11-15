from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryActivationGuard, MemoryReleaseCheck
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-09", "D1", "D8", "R25")]
ROOT = Path(__file__).resolve().parents[3]
ACTOR = Actor("operator", "operator_1")
EDITOR = SessionContext("session_1", "editor", ACTOR, None, frozenset({Role.EDITOR}))


def release(identifier: str, *, label: str | None = None) -> CandidateRelease:
    documents = {}
    for name in ConfigName:
        if name == ConfigName.UNIT_MAPPING:
            raw = (ROOT / "config/unit-mapping.json").read_bytes()
        elif name.value in {"environment-hazards", "routine-schedule", "responder-flows"}:
            raw = (ROOT / f"docs/product/{name.value}.json").read_bytes()
        else:
            suffix = (
                "api/examples/kit-map.example.json"
                if name == ConfigName.KIT_MAP
                else f"api/examples/config/{name.value}.example.json"
            )
            loaded = json.loads((ROOT / "docs/product" / suffix).read_bytes())
            value = loaded if name == ConfigName.KIT_MAP else loaded["value"]
            if name == ConfigName.UI_COPY and label is not None:
                value["designed_states"]["unknown"] = label
            raw = json.dumps(value).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease(identifier, documents)


def station(
    tmp_path: Path,
) -> tuple[SQLiteDatabase, ConfigRepository, ReleaseActivationService, LoaderChecks]:
    db = database(tmp_path)
    db.migrate((ROOT / "hub/src/crew_health/config/adapters/migrations/0001_config.sql").read_text())
    checks = LoaderChecks(maximum=64)
    # These fakes isolate the activation protocol. Core/clinical validation executes in separate tests.
    for check_id in sorted(required_contributors()):
        checks.register(MemoryReleaseCheck(check_id, lambda candidate: ()))
    repository = ConfigRepository()
    service = ReleaseActivationService(
        repository, ConfigLoader(checks, max_document_bytes=8388608), db.ids, origin=lambda view: "SIM"
    )
    return db, repository, service, checks


def test_rejected_proposal_is_durable_without_active_pointer(tmp_path: Path) -> None:
    db, repository, service, checks = station(tmp_path)
    checks.register(
        MemoryReleaseCheck(
            "custom", lambda candidate: (Finding("custom", ConfigName.UI_COPY, "/", "test rejection"),)
        )
    )
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        assert not service.propose(uow, EDITOR, release("release_rejected")).passed
    with db.snapshot() as view:
        assert repository.wire(view, "release_rejected")["state"] == "rejected"
        with pytest.raises(ConfigInvalid):
            repository.active_id(view)
        assert repository.revision(view, "release_rejected").etag == '"release_rejected:1:0"'


def test_missing_and_stale_etag_and_role_do_not_mutate(tmp_path: Path) -> None:
    db, repository, service, _ = station(tmp_path)
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        service.propose(uow, EDITOR, release("release_new"))
    for expected, code in (("", ErrorCode.VERSION_REQUIRED), ('"wrong"', ErrorCode.STALE_VERSION)):
        with (
            pytest.raises(ProblemError) as error,
            db.begin(actor=ACTOR, correlation_id="correlation_2") as uow,
        ):
            service.activate(uow, EDITOR, release_id="release_new", expected_etag=expected)
        assert error.value.code == code
    unauthorized = SessionContext(
        "session_2", "crew", Actor("person", "person_1"), "person_1", frozenset({Role.CREW})
    )
    with pytest.raises(ProblemError) as error, db.begin(actor=ACTOR, correlation_id="correlation_3") as uow:
        service.activate(uow, unauthorized, release_id="release_new", expected_etag='"release_new:1:0"')
    assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    with db.snapshot() as view:
        assert repository.wire(view, "release_new")["state"] == "draft"


def test_guard_failure_and_outbox_failure_roll_back_every_effect(tmp_path: Path) -> None:
    db, repository, service, checks = station(tmp_path)
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        service.propose(uow, EDITOR, release("release_new"))
    checks.register_guard(
        MemoryActivationGuard(
            "stock", lambda uow, candidate: (Finding("stock", ConfigName.KIT_MAP, "/", "stock constraint"),)
        )
    )
    with pytest.raises(ConfigInvalid), db.begin(actor=ACTOR, correlation_id="correlation_2") as uow:
        service.activate(uow, EDITOR, release_id="release_new", expected_etag='"release_new:1:0"')
    checks.guards.clear()
    with pytest.raises(ProblemError), db.begin(actor=ACTOR, correlation_id="correlation_3") as uow:
        service.activate(uow, EDITOR, release_id="release_new", expected_etag='"release_new:1:0"')
    with db.snapshot() as view:
        assert repository.generation(view) == 0
        assert repository.wire(view, "release_new")["state"] == "draft"
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((1,),)


def test_missing_active_release_never_uses_demo_documents(tmp_path: Path) -> None:
    db, repository, _, _ = station(tmp_path)
    with db.snapshot() as view, pytest.raises(ConfigInvalid):
        SQLiteConfigReader(view, repository).active()


def test_raw_document_updates_are_forbidden(tmp_path: Path) -> None:
    db, _, service, _ = station(tmp_path)
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        service.propose(uow, EDITOR, release("release_new"))
    with pytest.raises(ProblemError), db.begin(actor=ACTOR, correlation_id="correlation_2") as uow:
        uow.connection.execute("UPDATE config_document SET raw=?", (b"{}",))


def test_complete_example_release_runs_owned_template_and_schema_checks() -> None:
    from crew_health.config.adapters.verification import SchemaCheck
    from crew_health.config.domain.templates import TemplateCheck

    candidate = release("release_examples")
    assert SchemaCheck.from_directory(ROOT / "contracts/api/schemas").check(candidate) == ()
    assert TemplateCheck().check(candidate) == ()


def install_events(db: SQLiteDatabase) -> None:
    from tempfile import TemporaryDirectory

    from crew_health.contracts_gen.build import effective_tree, verify_base, verify_overlay
    from crew_health.events.adapters.repository import EventRepository, EventValidator

    db.migrate((ROOT / "hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text())
    base = verify_base(ROOT)
    overlay = verify_overlay(ROOT, base)
    assert overlay is not None
    with TemporaryDirectory() as temporary:
        api = effective_tree(ROOT, overlay, overlay.effective(base), Path(temporary))
        validator = EventValidator(schema_directory=api / "schemas")
    events = EventRepository(settings=db.settings, ids=db.ids, validator=validator)
    db.event_sink = events.append


def test_atomic_activation_changes_future_pins_preserves_open_snapshot(tmp_path: Path) -> None:
    db, repository, service, _ = station(tmp_path)
    install_events(db)
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        service.propose(uow, EDITOR, release("release_old"))
        first = service.activate(uow, EDITOR, release_id="release_old", expected_etag='"release_old:1:0"')
        assert first.etag == '"release_old:2:1"'
    db.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
    with db.snapshot() as old:
        assert old.release.release_id == "release_old"
        old_label = old.release.document(ConfigName.UI_COPY).raw
        with db.begin(actor=ACTOR, correlation_id="correlation_2") as uow:
            service.propose(uow, EDITOR, release("release_new", label="Changed configured word"))
            second = service.activate(
                uow, EDITOR, release_id="release_new", expected_etag='"release_new:1:1"'
            )
            assert second.etag == '"release_new:2:2"'
        assert old.release.release_id == "release_old"
        assert old.release.document(ConfigName.UI_COPY).raw == old_label
        assert repository.active_id(old) == "release_old"
    with db.snapshot() as current:
        assert current.release.release_id == "release_new"
        assert b"Changed configured word" in current.release.document(ConfigName.UI_COPY).raw
        assert repository.wire(current, "release_old")["state"] == "superseded"
        assert current.connection.rows("SELECT count(*) FROM events_outbox", (), limit=1) == ((2,),)
        assert service.validate_active(current).release_id == "release_new"
    with pytest.raises(ProblemError) as error, db.begin(actor=ACTOR, correlation_id="correlation_3") as uow:
        service.activate(uow, EDITOR, release_id="release_old", expected_etag=first.etag)
    assert error.value.code == ErrorCode.STALE_VERSION


def test_live_example_activation_and_invalid_active_startup_fail_closed(tmp_path: Path) -> None:
    db, repository, service, checks = station(tmp_path)
    install_events(db)
    with db.begin(actor=ACTOR, correlation_id="correlation_1") as uow:
        service.propose(uow, EDITOR, release("release_example"))
    live = ReleaseActivationService(repository, service.loader, db.ids, origin=lambda view: "LIVE")
    with pytest.raises(ConfigInvalid), db.begin(actor=ACTOR, correlation_id="correlation_2") as uow:
        live.activate(uow, EDITOR, release_id="release_example", expected_etag='"release_example:1:0"')
    with db.begin(actor=ACTOR, correlation_id="correlation_3") as uow:
        service.activate(uow, EDITOR, release_id="release_example", expected_etag='"release_example:1:0"')
    del checks.checks["14"]
    with db.snapshot() as view, pytest.raises(ConfigInvalid):
        service.validate_active(view)


def test_http_config_surface_uses_durable_command_executor(tmp_path: Path) -> None:
    import asyncio

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from crew_health.config.api.router import ConfigHttpCommands, create_router
    from crew_health.kernel.commands import CommandIdentity, StoredResult
    from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
    from crew_health.platform.adapters.ledger import CommandLedger
    from crew_health.platform.application.commands import CommandExecutor

    class EditorVisibility:
        def authorize(
            self,
            snapshot: ReadSnapshot | UnitOfWork,
            session: SessionContext,
            identity: CommandIdentity,
            result: StoredResult,
        ) -> None:
            if Role.EDITOR not in session.roles:
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "editor role required")

    db, repository, service, _ = station(tmp_path)
    install_events(db)
    with db.begin(actor=ACTOR, correlation_id="bootstrap_1") as uow:
        service.propose(uow, EDITOR, release("release_bootstrap"))
        service.activate(uow, EDITOR, release_id="release_bootstrap", expected_etag='"release_bootstrap:1:0"')
    db.set_release_reader(lambda view: SQLiteConfigReader(view, repository).active())
    executor = CommandExecutor(
        transactions=db,
        ledger=CommandLedger(db.ids, settings=db.settings),
        visibility=EditorVisibility(),
        authorize=lambda session, operation: None,
    )
    commands = ConfigHttpCommands(execute=executor, service=service, repository=repository, ids=db.ids)
    metadata = json.loads((ROOT / "docs/product/api/examples/configuration/index.json").read_bytes())["value"]
    summaries = {ConfigName(item["name"]): item["basis_summary"] for item in metadata["documents"]}
    summaries[ConfigName.UNIT_MAPPING] = "D12 sourced UCUM mapping"
    app = FastAPI()
    app.include_router(
        create_router(
            snapshots=db,
            repository=repository,
            authenticate=lambda request: EDITOR,
            commands=commands,
            basis_summaries=summaries,
            model_release_id=lambda: None,
        )
    )

    async def run() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://station") as client:
            index = await client.get("/v1/config")
            assert index.status_code == 200
            assert len(index.json()["documents"]) == len(ConfigName)
            document = await client.get("/v1/config/ui-copy")
            assert document.status_code == 200
            assert (
                await client.get("/v1/config/ui-copy", headers={"If-None-Match": document.headers["ETag"]})
            ).status_code == 304
            body = {"documents": [{"name": "ui-copy", "document": document.json()["document"]}]}
            body["documents"][0]["document"]["designed_states"]["unknown"] = "HTTP configured label"
            headers = {"Idempotency-Key": "command_create_1", "Config-Release": "release_bootstrap"}
            proposal = await client.post("/v1/config/releases", json=body, headers=headers)
            assert proposal.status_code == 201
            assert (
                await client.post("/v1/config/releases", json=body, headers=headers)
            ).json() == proposal.json()
            identifier = proposal.json()["release_id"]
            etag = proposal.headers["ETag"]
            assert (await client.get("/v1/config/releases/" + identifier)).headers["ETag"] == etag
            activated = await client.post(
                "/v1/config/releases/" + identifier + "/activate",
                headers={"Idempotency-Key": "command_activate_1", "If-Match": etag},
            )
            assert activated.status_code == 200
            assert activated.headers["ETag"] != etag
            assert (await client.get("/v1/config/ui-copy")).json()["document"]["designed_states"][
                "unknown"
            ] == "HTTP configured label"
            replay = await client.post("/v1/config/releases", json=body, headers=headers)
            assert replay.status_code == 201
            assert replay.headers["ETag"] == etag
            assert replay.json()["state"] == "draft"
            first_page = await client.get("/v1/config/releases?limit=1")
            assert first_page.status_code == 200
            assert first_page.json()["page"]["has_more"] is True
            next_page = await client.get(
                "/v1/config/releases",
                params={"limit": "1", "cursor": first_page.json()["page"]["next_cursor"]},
            )
            assert next_page.status_code == 200
            assert next_page.json()["page"]["has_more"] is False
            assert next_page.json()["page"]["next_cursor"] is None

    asyncio.run(run())
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM config_release", (), limit=1) == ((2,),)
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((2,),)


def test_proposal_role_and_reactivation_state_enforced(tmp_path: Path) -> None:
    db, repository, service, _ = station(tmp_path)
    operator = SessionContext("session_operator", "operator", ACTOR, None, frozenset({Role.OPERATOR}))
    with pytest.raises(ProblemError) as error, db.begin(actor=ACTOR, correlation_id="role_1") as uow:
        service.propose(uow, operator, release("release_denied"))
    assert error.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    install_events(db)
    with db.begin(actor=ACTOR, correlation_id="role_2") as uow:
        service.propose(uow, EDITOR, release("release_active"))
        revision = service.activate(
            uow, operator, release_id="release_active", expected_etag='"release_active:1:0"'
        )
    with pytest.raises(ProblemError) as error, db.begin(actor=ACTOR, correlation_id="role_3") as uow:
        service.activate(uow, operator, release_id="release_active", expected_etag=revision.etag)
    assert error.value.code == ErrorCode.INVALID_TRANSITION
    with db.snapshot() as view:
        assert repository.generation(view) == 1


def test_activation_pointer_loss_rolls_back_guard_and_release(tmp_path: Path) -> None:
    db, repository, service, checks = station(tmp_path)
    with db.begin(actor=ACTOR, correlation_id="pointer_1") as uow:
        service.propose(uow, EDITOR, release("release_pointer"))

    def remove_pointer(uow: object, candidate: CandidateRelease) -> tuple[Finding, ...]:
        # Inject a faulty activation participant inside the shared writer.
        from typing import cast

        from crew_health.kernel.uow import UnitOfWork

        cast(UnitOfWork, uow).connection.execute("DELETE FROM config_activation WHERE singleton=1")
        return ()

    checks.register_guard(MemoryActivationGuard("fault_pointer", remove_pointer))
    with pytest.raises(ConfigInvalid), db.begin(actor=ACTOR, correlation_id="pointer_2") as uow:
        service.activate(uow, EDITOR, release_id="release_pointer", expected_etag='"release_pointer:1:0"')
    with db.snapshot() as view:
        assert repository.generation(view) == 0
        assert repository.wire(view, "release_pointer")["state"] == "draft"


def test_live_example_guard_requires_kit_and_rejects_only_example_flag() -> None:
    # Structural validation is independent and still mandatory in ConfigLoader.
    with pytest.raises(ConfigInvalid):
        ReleaseActivationService._live_example(CandidateRelease("release_missing", {}), "LIVE")
    raw = b"{}"
    minimal = CandidateRelease(
        "release_structural_unit",
        {ConfigName.KIT_MAP: ConfigDocumentView(ConfigName.KIT_MAP, hashlib.sha256(raw).hexdigest(), raw)},
    )
    ReleaseActivationService._live_example(minimal, "LIVE")


def test_release_requires_and_pins_unit_mapping_under_check_one(tmp_path: Path) -> None:
    from crew_health.config.adapters.verification import SchemaCheck

    complete = release("release_complete")
    missing = CandidateRelease(
        "release_missing",
        {name: view for name, view in complete.documents.items() if name != ConfigName.UNIT_MAPPING},
    )
    schemas = SchemaCheck.from_directory(ROOT / "contracts/api/schemas")
    checks = LoaderChecks(maximum=64)
    checks.register(schemas)
    loader = ConfigLoader(checks, max_document_bytes=8388608)
    findings = loader.validate(missing, origin="SIM").errors
    assert any(f.check_id == "1" and f.document == ConfigName.UNIT_MAPPING for f in findings)
    assert not any(f.check_id == "1" for f in loader.validate(complete, origin="SIM").errors)
    db, repository, service, _ = station(tmp_path)
    with db.begin(actor=ACTOR, correlation_id="pin_mapping") as uow:
        service.propose(uow, EDITOR, complete)
    with db.snapshot() as view:
        pin = repository.release(view, complete.release_id)
        assert pin.document(ConfigName.UNIT_MAPPING) == complete.documents[ConfigName.UNIT_MAPPING]
