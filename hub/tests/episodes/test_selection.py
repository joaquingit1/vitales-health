from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.episodes.application import ports
from crew_health.episodes.application.selection import SelectedEpisodeReader
from crew_health.episodes.module import Registry, register
from crew_health.kernel.errors import ProblemError

from .builders import GROUND, OFFICER, PATIENT, station


@pytest.mark.trace("V25", "V26", "R20")
def test_selected_episode_seam_is_registered() -> None:
    registry = DeclarationRegistry(max_ports=100, max_hooks=200)
    register(cast(Registry, registry))
    assert ports.SelectedEpisodeFacts in registry.providers


@pytest.mark.trace("V25", "V26", "R20")
def test_selected_fields_are_pinned_immutable_and_do_not_include_answer_values(tmp_path: Path) -> None:
    import json

    from .builders import ROOT

    s = station(tmp_path)
    ep = s.create()
    answer = s.answer(ep)
    s.facts.rows[0] = __import__("dataclasses").replace(s.facts.rows[0], value="private-answer-value-canary")
    reader = SelectedEpisodeReader(s.service)
    with s.db.snapshot() as view:
        before = view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1)
        selected = reader.read(
            view, PATIENT, episode_id=ep, fields=frozenset({"concern_text", "onset", "answer_refs"})
        )
        assert view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1) == before
    fields = {field.field_id: field for field in selected.fields}
    assert fields["concern_text"].value_text == "private-concern-canary"
    assert fields["answer_refs"].source_refs == (answer,)
    assert fields["answer_refs"].value_text == answer
    assert "private-answer-value-canary" not in repr(selected)
    assert fields["onset"].value_text is None and fields["onset"].missing_reason == "not_recorded"
    with pytest.raises(FrozenInstanceError):
        selected.revision = 999  # type: ignore[misc]
    raw = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    raw["selected_field_labels"]["concern_text"] = "new label on different release"
    s.service.rules.activate("release_two", json.dumps(raw).encode())
    with s.db.snapshot() as view:
        again = reader.read(view, PATIENT, episode_id=ep, fields=frozenset({"concern_text"}))
    assert again.fields[0].label == fields["concern_text"].label
    assert again.config_sha256 == selected.config_sha256
    with pytest.raises(TypeError):
        s.service.rules.rules("release_1").selected_field_labels["concern_text"] = "mutated"  # type: ignore[index]


@pytest.mark.trace("V25", "V26", "R20")
@pytest.mark.parametrize("fields", [frozenset(), frozenset({"journal"}), frozenset({"answers"})])
def test_selection_refuses_unsupported_fields(tmp_path: Path, fields: frozenset[str]) -> None:
    s = station(tmp_path)
    ep = s.create()
    with s.db.snapshot() as view, pytest.raises(ProblemError):
        SelectedEpisodeReader(s.service).read(view, PATIENT, episode_id=ep, fields=fields)


@pytest.mark.trace("V25", "V26", "R20")
def test_selection_checks_field_grants_and_never_exposes_ground_live_facts(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    reader = SelectedEpisodeReader(s.service)
    with s.db.snapshot() as view:
        assert (
            reader.read(view, OFFICER, episode_id=ep, fields=frozenset({"answer_refs"})).fields[0].source_refs
        )
        s.facts.shared = False
        with pytest.raises(ProblemError):
            reader.read(view, OFFICER, episode_id=ep, fields=frozenset({"concern_text"}))
        s.facts.consent = True
        with pytest.raises(ProblemError):
            reader.read(view, GROUND, episode_id=ep, fields=frozenset({"concern_text"}))


@pytest.mark.trace("V25", "V26", "R20")
def test_selection_has_explicit_empty_and_missing_answers(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    reader = SelectedEpisodeReader(s.service)
    with s.db.snapshot() as view:
        empty = reader.read(view, PATIENT, episode_id=ep, fields=frozenset({"answer_refs"}))
        assert empty.fields[0].source_refs == () and empty.fields[0].missing_reason == "not_recorded"
        s.service.providers.answers = None
        with pytest.raises(ProblemError) as failure:
            reader.read(view, PATIENT, episode_id=ep, fields=frozenset({"answer_refs"}))
        assert failure.value.retryable


@pytest.mark.trace("V25", "V26", "R20")
def test_selection_validates_onset_precision_and_source_identity(tmp_path: Path) -> None:
    from dataclasses import replace

    from .builders import NOW

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "correct_onset", "onset": {"precision": "hour", "at": NOW.isoformat()}})
    with s.db.snapshot() as view:
        selected = SelectedEpisodeReader(s.service).read(
            view, PATIENT, episode_id=ep, fields=frozenset({"onset"})
        )
        assert selected.fields[0].value_text is not None and selected.fields[0].value_text.endswith("(hour)")
        assert selected.fields[0].observed_at == NOW
        s.facts.rows[0] = replace(s.facts.rows[0], patient_id="person_b")
        with pytest.raises(ProblemError):
            SelectedEpisodeReader(s.service).read(
                view, PATIENT, episode_id=ep, fields=frozenset({"answer_refs"})
            )


@pytest.mark.trace("T-E15-042")
def test_selected_labels_are_validated_before_activation(tmp_path: Path) -> None:
    import json

    from .builders import ROOT

    s = station(tmp_path)
    raw = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    del raw["selected_field_labels"]["onset"]
    with pytest.raises(ProblemError):
        s.service.rules.activate("bad_labels", json.dumps(raw).encode())
    assert s.service.rules.active_release_id == "release_1"
