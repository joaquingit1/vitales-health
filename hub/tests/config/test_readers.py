"""SYSTEM 5.6: readers bound to a UnitOfWork or ReadSnapshot see the release active at its start, and the ui-copy
unknown text is parsed once per ui-copy version."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from crew_health.config.adapters import readers as readers_module
from crew_health.config.adapters.readers import ConfigReaders, UnknownTextCache
from crew_health.config.adapters.repository import ConfigRepository
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.domain.checks import parse_document
from crew_health.contracts_gen.enums import ConfigName
from crew_health.platform.storage import SQLiteDatabase

from .test_activation import ACTOR, EDITOR, install_events, release, station

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-09", "SYSTEM-5.6")]
FIELD = "ui-copy/duration_words/minutes"


@dataclass(frozen=True)
class Hub:
    db: SQLiteDatabase
    repository: ConfigRepository
    service: ReleaseActivationService
    readers: ConfigReaders

    def activate(self, identifier: str, label: str) -> None:
        with self.db.begin(actor=ACTOR, correlation_id=f"cor_{identifier}") as uow:
            self.service.propose(uow, EDITOR, release(identifier, label=label))
            etag = self.repository.revision(uow, identifier).etag
            self.service.activate(uow, EDITOR, release_id=identifier, expected_etag=etag)


def _hub(tmp_path: Path, *, cache: int = 4) -> Hub:
    db, repository, service, _ = station(tmp_path)
    install_events(db)
    hub = Hub(db, repository, service, ConfigReaders(db, repository, UnknownTextCache(cache)))
    hub.activate("release_a", "Unknown A")
    # As assembly does once a release is active: every view pins the release active when it began.
    db.set_release_reader(lambda view: repository.release(view, repository.active_id(view)))
    return hub


def test_one_view_sees_one_release_while_another_connection_activates(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    db, readers = hub.db, hub.readers
    with db.snapshot() as view:
        bound = readers.bind(view)
        assert bound.active().release_id == "release_a"
        # Another connection activates release_b between two reads of the same command.
        worker = threading.Thread(target=hub.activate, args=("release_b", "Unknown B"))
        worker.start()
        worker.join(10)
        assert not worker.is_alive()
        assert readers.active().release_id == "release_b"  # a new snapshot already sees it
        assert bound.active().release_id == "release_a"
        copy = parse_document(bound.document(ConfigName.UI_COPY).raw)
        assert isinstance(copy, dict) and copy["designed_states"]["unknown"] == "Unknown A"
        assert bound.render(FIELD, "{n} min", {}) == "Unknown A min"
    with db.snapshot() as view:
        assert readers.bind(view).render(FIELD, "{n} min", {}) == "Unknown B min"


def test_a_unit_of_work_binds_the_release_active_at_its_start(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    db, readers = hub.db, hub.readers
    with db.begin(actor=ACTOR, correlation_id="cor_bound") as uow:
        bound = readers.bind(uow)
        assert bound.active().release_id == "release_a"
        assert bound.render(FIELD, "{n} min", {}) == "Unknown A min"
        assert bound.render(FIELD, "{n} min", {"n": "3"}) == "3 min"


def test_ui_copy_is_parsed_once_per_release_and_the_cache_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _hub(tmp_path, cache=1)
    db, readers = hub.db, hub.readers
    parses: list[bytes] = []

    def counting(raw: bytes) -> object:
        parses.append(raw)
        return parse_document(raw)

    monkeypatch.setattr(readers_module, "parse_document", counting)
    for _ in range(3):
        with db.snapshot() as view:
            bound = readers.bind(view)
            assert bound.render(FIELD, "{n} min", {}) == "Unknown A min"
            assert bound.render(FIELD, "{n} min", {}) == "Unknown A min"
        assert readers.render(FIELD, "{n} min", {}) == "Unknown A min"
    assert len(parses) == 1
    hub.activate("release_b", "Unknown B")
    assert readers.render(FIELD, "{n} min", {}) == "Unknown B min"
    assert readers.render(FIELD, "{n} min", {}) == "Unknown B min"
    assert len(parses) == 2
    with db.snapshot() as view:  # release_a's entry left the one-entry cache: reading it again parses again
        assert readers.bind(view).release("release_a").release_id == "release_a"
        assert readers.copies.text(readers.bind(view).release("release_a")) == "Unknown A"
    assert len(parses) == 3
