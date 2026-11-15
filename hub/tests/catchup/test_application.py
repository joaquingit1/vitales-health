"""SYSTEM 8.1 and 5.7: the press-time composition is the approved record, reads never write, bounded providers."""

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.catchup.application.bounded import BoundedCall
from crew_health.catchup.application.composer import CatchUpComposer
from crew_health.catchup.application.log import GroundLogService
from crew_health.catchup.application.ports import GROUND_LOG_SCOPE
from crew_health.catchup.domain.errors import CatchUpUnavailable, CompositionTooLarge
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor

from .support import ROOT, Harness, event, feed, message_event
from .test_compose import Snapshot
from .test_http import deliver, log


def state(h: Harness, ref: str = "msg_1") -> str:
    with h.db.snapshot() as view:
        row = h.catchup.deps.repository.message(view, ref)
        assert row is not None
        return row.state


def lines_count(h: Harness) -> int:
    with h.db.snapshot() as view:
        return len(view.connection.rows("SELECT text FROM catchup_line", (), limit=64))


def subscribe(h: Harness, message: object) -> None:
    with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_subscriber") as uow:
        h.catchup.composer.on_ground_message_changed(uow, message)  # type: ignore[arg-type]


def changed_ui_release(h: Harness) -> MemoryRelease:
    ui = json.loads((ROOT / "config/ui-copy.json").read_bytes())
    ui["catch_up_words"]["step_done_line"] = "CHANGED {time} {step}"
    raw = json.dumps(ui).encode()
    documents = dict(h.release.documents)
    documents[ConfigName.UI_COPY] = ConfigDocumentView(
        ConfigName.UI_COPY, hashlib.sha256(raw).hexdigest(), raw
    )
    return MemoryRelease("release_2", documents)


@pytest.mark.trace("R61", "E29-04", "D42")
def test_release_change_between_press_and_subscriber_never_changes_the_approved_catch_up(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    at_press = h.composer.for_help(Snapshot())
    changed = changed_ui_release(h)
    h.versions.add(changed)
    h.release = changed
    feed(h, message_event())
    assert state(h) == "frozen"
    assert h.composer.for_help(Snapshot()) == at_press
    with h.db.snapshot() as view:
        row = h.catchup.deps.repository.message(view, "msg_1")
        assert row is not None and row.pinned_versions is not None
        assert json.loads(row.pinned_versions)["ui-copy"] == Snapshot().pins[0].version
        assert row.subject_person_id == "person_tomas"


@pytest.mark.trace("R61", "E29-04")
def test_preview_writes_nothing_and_the_frozen_copy_outlives_restart_and_missing_documents(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    shown = h.composer.for_help(Snapshot())
    with h.db.snapshot() as view:
        assert h.catchup.deps.repository.for_revision(view, "help_1", 1) is None
    feed(h, message_event())
    h.release = MemoryRelease("release_missing", {})
    restarted = CatchUpComposer(replace(h.catchup.deps, config=None))
    assert restarted.for_help(replace(Snapshot(), subject_name="Altered")) == shown


@pytest.mark.trace("R61", "E29-04")
def test_ground_log_of_a_pending_item_renders_the_pinned_composition_and_writes_nothing(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    composer = h.catchup.composer
    composer.deps = replace(composer.deps, help_snapshots=None)
    feed(h, message_event())
    composer.deps = replace(composer.deps, help_snapshots=h.snapshots)
    assert state(h) == "pending"
    deliver(h)
    first = log(h)["items"]
    second = log(h)["items"]
    assert first == second
    assert isinstance(first, list) and first[0]["catch_up"]["what_was_done"][0] == "12:02 Do not rub the eye."
    assert state(h) == "pending" and lines_count(h) == 0


@pytest.mark.trace("R61", "E29-04")
@pytest.mark.parametrize("failure", ["timeout", "error", "missing", "other_session"])
def test_a_degraded_snapshot_read_is_a_retryable_error_never_unknown_content(
    tmp_path: Path, failure: str
) -> None:
    h = Harness(tmp_path, port_timeout_ms=30)
    composer = h.catchup.composer
    composer.deps = replace(composer.deps, help_snapshots=None)
    feed(h, message_event())
    composer.deps = replace(composer.deps, help_snapshots=h.snapshots)
    deliver(h)
    gate = threading.Event()
    if failure == "timeout":
        h.snapshots.block = gate
    elif failure == "error":
        h.snapshots.failure = RuntimeError("private clinical text")
    elif failure == "missing":
        h.snapshots.value = None
    else:
        h.snapshots.value = replace(Snapshot(), help_session_id="other_help")
    try:
        response = h.client.get("/v1/ground/log", headers={"role": "ground"})
    finally:
        gate.set()
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR" and response.json()["retryable"] is True
    assert "private" not in response.text and "Unknown" not in response.text
    assert state(h) == "pending" and lines_count(h) == 0


@pytest.mark.trace("R61", "E29-04")
def test_storage_error_from_help_snapshot_propagates_and_is_never_quarantined(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.snapshots.failure = StorageUnavailable("disk_full")
    for _ in range(5):
        with pytest.raises(StorageUnavailable):
            feed(h, message_event())
    assert h.catchup.quarantined(10) == ()


@pytest.mark.trace("R61", "E29-04")
def test_a_bad_queued_event_is_retried_then_quarantined_and_the_next_event_still_freezes(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    malformed = replace(message_event("msg_bad"), data_json=b"{", event_id="evt_bad")
    for _ in range(2):
        with pytest.raises(ProblemError):
            subscribe(h, malformed)
    subscribe(h, malformed)
    (record,) = h.catchup.quarantined(10)
    assert (record.event_id, record.subscriber, record.reason, record.attempts) == (
        "evt_bad",
        "catchup.ground_message",
        "INVALID_VALUE",
        3,
    )
    feed(h, message_event())
    assert state(h) == "frozen"


@pytest.mark.trace("R61", "E29-04")
def test_quarantine_rolls_back_the_handler_writes_of_the_bad_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.snapshots.value = None
    bad = message_event()
    for _ in range(3):
        try:
            subscribe(h, bad)
        except CatchUpUnavailable:
            pass
    with h.db.snapshot() as view:
        assert h.catchup.deps.repository.message(view, "msg_1") is None
    assert [r.reason for r in h.catchup.quarantined(10)] == ["CatchUpUnavailable"]


@pytest.mark.trace("R61", "E29-04")
def test_optional_calls_are_thread_safe_and_busy_fails_fast() -> None:
    call = BoundedCall(20)
    entered, release = threading.Event(), threading.Event()

    def blocking() -> str:
        entered.set()
        release.wait(1)
        return "late"

    try:
        with pytest.raises(CatchUpUnavailable):
            call.call(blocking)
        assert entered.wait(1)
        with pytest.raises(CatchUpUnavailable):
            call.call(lambda: "must not start")
    finally:
        release.set()

    pending = call._pending
    assert pending is not None and pending.done.wait(1)
    assert call.call(lambda: "fresh") == "fresh"


@pytest.mark.trace("R61", "E29-04")
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE catchup_line SET text='Changed'",
        "DELETE FROM catchup_line",
        "DELETE FROM catchup_message",
        "UPDATE catchup_message SET title='Changed'",
        "INSERT INTO catchup_line VALUES ('msg_1', 'what_happened', 2, 'Extra')",
    ],
)
def test_database_forbids_changing_approved_composition(tmp_path: Path, sql: str) -> None:
    h = Harness(tmp_path)
    feed(h, message_event())
    approved = h.composer.for_help(Snapshot())
    with pytest.raises(ProblemError):
        with h.db.begin(actor=Actor("engine", "probe"), correlation_id="cor_probe") as uow:
            uow.connection.execute(sql)
    assert h.composer.for_help(Snapshot()) == approved


@pytest.mark.trace("R61", "E29-04")
def test_freeze_rolls_back_all_lines_when_transaction_fails(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    composer = h.catchup.composer
    composer.deps = replace(composer.deps, help_snapshots=None)
    feed(h, message_event())
    composition, pinned = composer.compose(Snapshot())
    with pytest.raises(StorageUnavailable):
        with h.db.begin(actor=Actor("engine", "probe"), correlation_id="cor_probe") as uow:
            h.catchup.deps.repository.freeze(uow, "msg_1", composition, pinned, "person_tomas")
            raise StorageUnavailable("disk_full")
    assert state(h) == "pending" and lines_count(h) == 0


@pytest.mark.trace("R61", "E29-04")
def test_subscriber_ignores_nonqueued_noncrew_and_wrong_type(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    queued = message_event()
    for changed in (
        replace(queued, scope=replace(queued.scope, kind="ground")),
        replace(queued, type=EventType.GROUND_LINK_CHANGED),
        event(EventType.GROUND_MESSAGE_CHANGED, {"state": "sent"}),
    ):
        subscribe(h, changed)
    with h.db.snapshot() as view:
        assert h.catchup.deps.repository.message(view, "msg_1") is None
    feed(h, queued)
    feed(h, queued)
    assert state(h) == "frozen"


@pytest.mark.trace("R61", "E29-04")
def test_composition_limit_errors_without_freezing_or_truncating(tmp_path: Path) -> None:
    h = Harness(tmp_path, max_lines=2)
    with pytest.raises(CompositionTooLarge):
        h.composer.for_help(Snapshot())
    with pytest.raises(CompositionTooLarge):
        feed(h, message_event())
    with h.db.snapshot() as view:
        assert h.catchup.deps.repository.message(view, "msg_1") is None


@pytest.mark.trace("R61", "E29-04")
def test_link_unknown_never_fabricates_a_link_and_wire_matches_provider(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    response = h.client.get("/v1/ground-link")
    assert h.composer.link().contract == response.json()
    assert h.composer.link().simulated is True
    composer = CatchUpComposer(replace(h.catchup.deps, link=None))
    with pytest.raises(CatchUpUnavailable):
        composer.link()
    with pytest.raises(CatchUpUnavailable):
        GroundLogService(composer.deps, composer).log(None, recipient="person_ground_1")


@pytest.mark.trace("R61", "E29-04")
def test_storage_error_from_a_timed_out_call_is_raised_on_next_attempt() -> None:
    call = BoundedCall(10)
    release, finished = threading.Event(), threading.Event()

    def disk_error() -> str:
        release.wait(1)
        finished.set()
        raise StorageUnavailable("disk_full")

    try:
        with pytest.raises(CatchUpUnavailable):
            call.call(disk_error)
    finally:
        release.set()
    assert finished.wait(1)
    pending = call._pending
    assert pending is not None and pending.done.wait(1)
    with pytest.raises(StorageUnavailable):
        call.call(lambda: "must not hide a storage failure")


@pytest.mark.trace("R61", "E29-04")
def test_lower_new_composition_limit_never_hides_frozen_lines(tmp_path: Path) -> None:
    from crew_health.catchup.adapters.sqlite_repository import SqliteCatchUpRepository

    h = Harness(tmp_path)
    feed(h, message_event())
    approved = h.composer.for_help(Snapshot())
    restarted = CatchUpComposer(
        replace(
            h.catchup.deps,
            settings=replace(h.catchup.deps.settings, max_lines=2),
            repository=SqliteCatchUpRepository(2),
        )
    )
    assert restarted.for_help(Snapshot()) == approved
    with pytest.raises(CompositionTooLarge):
        restarted.for_help(replace(Snapshot(), session_revision=2))


@pytest.mark.trace("R61", "E29-04")
def test_header_cannot_be_truncated_to_fit_a_one_line_section(tmp_path: Path) -> None:
    h = Harness(tmp_path, max_lines=1)
    with pytest.raises(CompositionTooLarge):
        h.composer.for_help(replace(Snapshot(), ticks=(), crew_called_at=None))


@pytest.mark.trace("R61", "E29-04")
def test_absent_link_facts_fail_closed_even_when_query_is_bound(tmp_path: Path) -> None:
    from crew_health.groundlink.application.ports import GroundLinkView, LinkGap
    from crew_health.kernel.uow import ReadSnapshot

    class UnknownLink:
        def current(self, view: ReadSnapshot) -> GroundLinkView | None:
            return None

        def last_gap(self, view: ReadSnapshot) -> LinkGap | None:
            return None

    h = Harness(tmp_path)
    composer = CatchUpComposer(replace(h.catchup.deps, link=UnknownLink()))
    with pytest.raises(CatchUpUnavailable):
        composer.link()


@pytest.mark.trace("R61", "E29-04")
def test_reused_message_identity_with_changed_metadata_is_an_error(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    feed(h, message_event())
    other = replace(Snapshot(), help_session_id="other_help")
    h.snapshots.values["msg_1"] = other
    with pytest.raises(CatchUpUnavailable):
        feed(h, message_event(snapshot=other))
    assert state(h) == "frozen"


@pytest.mark.trace("R61", "E29-04")
def test_wrong_snapshot_identity_cannot_freeze_another_message(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.snapshots.value = replace(Snapshot(), help_session_id="other_help")
    with pytest.raises(CatchUpUnavailable):
        feed(h, message_event())
    with h.db.snapshot() as view:
        assert h.catchup.deps.repository.message(view, "msg_1") is None


@pytest.mark.trace("R61", "E29-04")
def test_repository_bounds_inventory_and_rejects_missing_approved_content(tmp_path: Path) -> None:
    from crew_health.catchup.adapters.sqlite_repository import SqliteCatchUpRepository

    h = Harness(tmp_path)
    repo = h.catchup.deps.repository
    assert isinstance(repo, SqliteCatchUpRepository)
    second = replace(Snapshot(), help_session_id="help_2")
    h.snapshots.values["msg_2"] = second
    feed(h, message_event())
    feed(h, message_event("msg_2", snapshot=second))
    with h.db.snapshot() as view:
        assert [row.ground_message_id for row in repo.rows(view, limit=1)] == ["msg_1"]
        with pytest.raises(CatchUpUnavailable):
            repo.lines(view, "missing")
    composition, pinned = h.catchup.composer.compose(Snapshot())
    with pytest.raises(CatchUpUnavailable):
        with h.db.begin(actor=Actor("engine", "probe"), correlation_id="cor_probe") as uow:
            repo.freeze(uow, "missing", composition, pinned, None)


@pytest.mark.trace("R61", "E29-04", "EI5")
def test_ground_log_shows_only_items_the_named_recipient_is_granted(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    second = replace(Snapshot(), help_session_id="help_2", subject_person_id="person_mara")
    h.snapshots.values["msg_2"] = second
    feed(h, message_event())
    feed(h, message_event("msg_2", snapshot=second))
    deliver(h)
    h.consent.denied.add(("person_mara", "person_ground_1"))
    items = log(h)["items"]
    assert isinstance(items, list) and [i["ref_id"] for i in items] == ["msg_1"]
    assert {c[2] for c in h.consent.calls} == {GROUND_LOG_SCOPE}
    assert {c[1] for c in h.consent.calls} == {"person_ground_1"}
    unnamed = h.client.get("/v1/ground/log", headers={"role": "ground", "person": "none"})
    assert unnamed.status_code == 403 and unnamed.json()["code"] == ErrorCode.UNAUTHORIZED_SCOPE.value


@pytest.mark.trace("R61", "E29-04", "EI5")
def test_ground_log_fails_closed_without_a_consent_provider(tmp_path: Path) -> None:
    h = Harness(tmp_path, consent=False)
    feed(h, message_event())
    deliver(h)
    response = h.client.get("/v1/ground/log", headers={"role": "ground"})
    assert response.status_code == 403 and response.json()["code"] == ErrorCode.CONSENT_REQUIRED.value
    assert "items" not in response.json()
