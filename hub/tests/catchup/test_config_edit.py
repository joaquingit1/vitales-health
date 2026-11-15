"""R61: release edits change content, and invalid documents never compose guidance.

Catch-up reads the document versions the help session pinned (SYSTEM 8.1), so an edited release changes the words
of sessions pinned to it and never the words of an open session pinned to an older one.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.catchup.application.config import parse_words
from crew_health.catchup.domain.compose import PlanFacts, compose_for_help
from crew_health.catchup.domain.errors import CatchUpUnavailable
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease

from .support import Harness, release, validate_document
from .test_compose import Pin, Snapshot


def with_ui(original: MemoryRelease, raw: bytes, release_id: str = "release_edited") -> MemoryRelease:
    documents = dict(original.documents)
    documents[ConfigName.UI_COPY] = ConfigDocumentView(
        ConfigName.UI_COPY, hashlib.sha256(raw).hexdigest(), raw
    )
    return MemoryRelease(release_id, documents)


def edited_release(key: str, value: str) -> MemoryRelease:
    original = release()
    data = json.loads(original.document(ConfigName.UI_COPY).raw)
    data["catch_up_words"][key] = value
    validate_document("config-ui-copy.schema.json", data)
    return with_ui(original, json.dumps(data).encode())


def pinned_to(item: MemoryRelease) -> Snapshot:
    return replace(
        Snapshot(),
        pins=tuple(
            Pin(name.value, item.document(name).version)
            for name in (ConfigName.UI_COPY, ConfigName.HELP_PRESENTATION)
        ),
    )


def words_of(item: MemoryRelease) -> tuple[bytes, bytes]:
    return item.document(ConfigName.UI_COPY).raw, item.document(ConfigName.HELP_PRESENTATION).raw


@pytest.mark.trace("R61")
def test_edit_step_template_changes_a_session_pinned_to_the_new_release_without_code_change(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    edited = edited_release("step_done_line", "At {time}: {step}")
    h.versions.add(edited)
    h.release = edited
    before = h.composer.for_help(Snapshot())
    after = h.composer.for_help(pinned_to(edited))
    assert before.what_was_done[0] == "12:02 Do not rub the eye."
    assert after.what_was_done[0] == "At 12:02: Do not rub the eye."
    assert before.what_happened == after.what_happened


@pytest.mark.trace("R61")
def test_edit_nothing_watched_changes_known_empty_plan_text() -> None:
    before = parse_words(*words_of(release()))
    after = parse_words(*words_of(edited_release("nothing_watched", "No watch steps.")))
    plan = PlanFacts((), (), (), ())
    a = compose_for_help(Snapshot(), before.catch, before.ground, crew_time_text=None, plan=plan)
    b = compose_for_help(Snapshot(), after.catch, after.ground, crew_time_text=None, plan=plan)
    assert a.being_watched == ("Nothing is being watched.",)
    assert b.being_watched == ("No watch steps.",)


@pytest.mark.trace("R61")
@pytest.mark.parametrize("bad", [b"{", b"{}"])
def test_invalid_pinned_ui_copy_raises_for_help_to_fall_back(tmp_path: Path, bad: bytes) -> None:
    h = Harness(tmp_path)
    broken = with_ui(h.release, bad, "release_bad")
    h.versions.add(broken)
    with pytest.raises(ProblemError) as error:
        h.composer.for_help(pinned_to(broken))
    assert error.value.code == ErrorCode.CONFIG_INVALID


@pytest.mark.trace("R61")
def test_missing_pin_or_unknown_version_is_unavailable_never_the_active_release(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with pytest.raises(CatchUpUnavailable):
        h.composer.for_help(replace(Snapshot(), pins=(Pin("help-presentation", "x" * 64),)))
    with pytest.raises(CatchUpUnavailable):
        h.composer.for_help(
            replace(Snapshot(), pins=(Pin("ui-copy", "0" * 64), Pin("help-presentation", "1" * 64)))
        )
    no_reader = Harness(tmp_path / "no_reader", config=False)
    with pytest.raises(CatchUpUnavailable):
        no_reader.composer.for_help(Snapshot())


@pytest.mark.trace("R61")
def test_documents_validate_and_unknown_crew_time_stays_unknown(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    for name in (ConfigName.UI_COPY, ConfigName.HELP_PRESENTATION):
        validate_document(f"config-{name.value}.schema.json", json.loads(h.release.document(name).raw))
    lines = h.composer.for_help(replace(Snapshot(), crew_time_text=None))
    assert lines.what_was_done[-1] == "Crew called at Unknown. Mara Okafor is helping."


@pytest.mark.trace("R61")
def test_schema_optional_catch_up_words_still_required_for_composition(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    data = json.loads(h.release.document(ConfigName.UI_COPY).raw)
    data.pop("catch_up_words")
    validate_document("config-ui-copy.schema.json", data)
    stripped = with_ui(h.release, json.dumps(data).encode(), "release_no_catch_words")
    h.versions.add(stripped)
    with pytest.raises(ProblemError) as error:
        h.composer.for_help(pinned_to(stripped))
    assert error.value.code == ErrorCode.CONFIG_INVALID
