from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.episodes.application.dto import LinkedFact
from crew_health.episodes.application.effects import RunCorrectionEffects
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryHealth
from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork

from .builders import NOW, PATIENT, station


class Assessments:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.stale: list[str] = []

    def citing(
        self, uow: ReadSnapshot | UnitOfWork, episode_id: str, record_ids: Sequence[str]
    ) -> Sequence[str]:
        if self.fail:
            raise OSError("down")
        return ("asm_one",)

    def ids_for_episode(self, uow: ReadSnapshot | UnitOfWork, episode_id: str) -> Sequence[str]:
        return ("asm_one",)

    def mark_stale(self, uow: UnitOfWork, assessment_id: str, cause_ref: str) -> None:
        self.stale.append(assessment_id)

    def mark_reviewed(self, uow: UnitOfWork, assessment_id: str, review_id: str) -> None:
        pass


@pytest.mark.trace("T-E15-015", "T-E15-017", "T-E15-028", "V30")
@pytest.mark.parametrize("fails", [False, True])
def test_correction_intent_survives_and_has_fail_safe(tmp_path: Path, fails: bool) -> None:
    s = station(tmp_path)
    ep = s.create()
    old = s.answer(ep)
    with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_model") as uow:
        s.handlers.linked(
            uow,
            LinkedFact("event_assessment", ep, "assessment", "asm_one", "draft", "SIM", NOW, is_model=True),
        )
    s.answer(ep, supersedes=old)
    provider = Assessments(fail=fails)
    s.service.providers.assessments = provider
    s.service.providers.assessment_commands = provider
    s.service.limits = replace(s.service.limits, effect_max_attempts=1)
    health = MemoryHealth(maximum=8)
    worker = RunCorrectionEffects(s.service, s.db, s.db, health)
    try:
        assert worker.run() == 1
        assert worker.run() == 0
    finally:
        worker.close()
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "awaiting_review"
        assert len(s.service.repo.task_facts(view, ep)) == 1
        effects = view.connection.rows("SELECT state FROM episodes_effect", (), limit=1)
        assert effects == (("failed" if fails else "done",),)
    assert provider.stale == ([] if fails else ["asm_one"])
    assert health.snapshot().status == ("degraded" if fails else "ok")


@pytest.mark.trace("T-E15-009", "T-E15-021", "D08")
def test_duplicate_and_late_links_do_not_overwrite_newer_state(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    new = LinkedFact(
        "new_event", ep, "measurement_request", "mrq_one", "completed", "SIM", NOW + timedelta(seconds=1)
    )
    old = LinkedFact("old_event", ep, "measurement_request", "mrq_one", "in_progress", "SIM", NOW)
    for event in (new, new, old):
        with s.db.begin(actor=Actor("engine", "engine_one"), correlation_id="corr_link") as uow:
            s.handlers.linked(uow, event)
    with s.db.snapshot() as view:
        assert s.service.repo.links(view, ep) == (("measurement_request", "mrq_one", "completed"),)
        assert s.service.repo.require(view, ep).revision == 4


@pytest.mark.trace("T-E15-020", "T-E15-048")
def test_task_owner_reassignment_and_closure_matrix(tmp_path: Path) -> None:
    from .builders import OFFICER

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    with s.db.snapshot() as view:
        task = s.queries.tasks(view, ep)[0]
    with pytest.raises(ProblemError):
        s.act(
            ep,
            {
                "action": "reassign_followup",
                "task_id": task["task_id"],
                "task_revision": 1,
                "owner_person_id": "person_a",
            },
            session=OFFICER,
        )
    s.act(
        ep,
        {
            "action": "cancel_followup",
            "task_id": task["task_id"],
            "task_revision": 1,
            "cancellation_reason": "token",
        },
        session=OFFICER,
    )
    s.act(ep, {"action": "add_followup", "action_text": "token"})
    with s.db.snapshot() as view:
        custom = next(t for t in s.queries.tasks(view, ep) if t["kind"] == "custom")
    s.act(ep, {"action": "complete_followup", "task_id": custom["task_id"], "task_revision": 1})
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "open"


@pytest.mark.trace("T-E15-010", "T-E15-041")
def test_task_history_and_pins_survive_activation(tmp_path: Path) -> None:
    import json

    from crew_health.episodes.application.codec import decode, obj

    from .builders import OFFICER, ROOT

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "request_review"})
    with s.db.snapshot() as view:
        task = s.queries.tasks(view, ep)[0]
    original_action = task["action"]
    raw = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    raw["task_templates"]["review_requested"] = "changed {time}"
    raw["due_policies"][0]["duration"]["value"] = 12
    s.service.rules.activate("release_two", json.dumps(raw).encode())
    s.act(
        ep,
        {
            "action": "reassign_followup",
            "task_id": task["task_id"],
            "task_revision": 1,
            "owner_person_id": "officer_b",
        },
        session=OFFICER,
    )
    with s.db.snapshot() as view:
        history = view.connection.rows(
            "SELECT data FROM episodes_task_event WHERE task_id=? ORDER BY seq",
            (str(task["task_id"]),),
            limit=10,
        )
        assert [obj(decode(row[0])["owner"])["id"] for row in history] == ["officer_a", "officer_b"]
        current = s.queries.tasks(view, ep)[0]
        assert current["action"] == original_action
        assert current["due_at"] is None
        assert current["config_release_id"] == "release_1"


@pytest.mark.trace("T-E15-016", "T-E15-028", "V30")
def test_correction_reopens_a_resolution_that_cited_observation(tmp_path: Path) -> None:
    from crew_health.episodes.application.dto import ObservationFact

    class Observations:
        def get(self, uow: UnitOfWork, observation_id: str) -> ObservationFact | None:
            return ObservationFact(observation_id, "person_a", ep, "accepted", NOW + timedelta(seconds=1))

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.service.providers.observations = Observations()
    s.act(
        ep,
        {
            "action": "resolve",
            "reason": "measurement_anomaly_not_reproduced",
            "rationale": "token",
            "observation_ids": ["obs_one"],
        },
    )
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_correction") as uow:
        s.handlers.correction(uow, ep, "obs_two", "obs_one")
    provider = Assessments()
    s.service.providers.assessments = provider
    s.service.providers.assessment_commands = provider
    health = MemoryHealth(maximum=8)
    worker = RunCorrectionEffects(s.service, s.db, s.db, health)
    try:
        assert worker.run() == 1
    finally:
        worker.close()
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "awaiting_review"
        assert len(s.queries.history(view, "reopen", ep)) == 1


@pytest.mark.trace("T-E15-015", "T-E15-017")
@pytest.mark.parametrize("dependency", ["missing", "no_commands", "overflow", "timeout"])
def test_correction_failures_are_bounded_and_leave_retryable_work(tmp_path: Path, dependency: str) -> None:
    from threading import Event

    s = station(tmp_path)
    ep = s.create()
    old = s.answer(ep)
    s.answer(ep, supersedes=old)
    gate = Event()

    class Slow(Assessments):
        def citing(
            self, uow: ReadSnapshot | UnitOfWork, episode_id: str, record_ids: Sequence[str]
        ) -> Sequence[str]:
            if dependency == "timeout":
                gate.wait(1)
            return tuple("asm_" + str(i) for i in range(1001)) if dependency == "overflow" else ("asm_one",)

    provider = Slow()
    s.service.providers.assessments = None if dependency == "missing" else provider
    s.service.providers.assessment_commands = None if dependency == "no_commands" else provider
    s.service.limits = replace(s.service.limits, effect_max_attempts=2, effect_call_timeout_ms=5)
    worker = RunCorrectionEffects(s.service, s.db, s.db, MemoryHealth(maximum=8))
    try:
        assert worker.run() == 1
        if dependency == "timeout":
            assert worker.run() == 0
        with s.db.snapshot() as view:
            assert view.connection.rows("SELECT state,attempts FROM episodes_effect", (), limit=1) == (
                ("due", 1),
            )
    finally:
        gate.set()
        worker.close()


@pytest.mark.trace("T-E15-018", "T-E15-028")
def test_maintenance_starts_worker_and_arms_durable_wake(tmp_path: Path) -> None:
    from crew_health.episodes.application.maintenance import EpisodeMaintenance

    s = station(tmp_path)
    health = MemoryHealth(maximum=8)
    effects = RunCorrectionEffects(s.service, s.db, s.db, health)
    maintenance = EpisodeMaintenance(s.service, effects, s.db, health)
    try:
        maintenance.start()
        assert maintenance.future is not None
        assert maintenance.future.result(timeout=2) == 0
        with s.db.snapshot() as view:
            assert view.connection.rows(
                "SELECT count(*) FROM platform_schedule WHERE key=?", ("episodes.maintenance",), limit=1
            ) == ((1,),)
    finally:
        maintenance.close()


@pytest.mark.trace("T-E15-028", "V30")
def test_storage_failure_is_not_optional_dependency_failure(tmp_path: Path) -> None:
    from crew_health.kernel.errors import StorageUnavailable

    class Corrupt(Assessments):
        def citing(
            self, uow: ReadSnapshot | UnitOfWork, episode_id: str, record_ids: Sequence[str]
        ) -> Sequence[str]:
            raise StorageUnavailable("corrupt")

    s = station(tmp_path)
    ep = s.create()
    old = s.answer(ep)
    s.answer(ep, supersedes=old)
    s.service.providers.assessments = Corrupt()
    worker = RunCorrectionEffects(s.service, s.db, s.db, MemoryHealth(maximum=8))
    try:
        with pytest.raises(StorageUnavailable):
            worker.run()
    finally:
        worker.close()
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT attempts,state FROM episodes_effect", (), limit=1) == (
            (0, "due"),
        )


@pytest.mark.trace("T-E15-028", "V30")
def test_timed_out_call_never_queues_a_second_call(tmp_path: Path) -> None:
    from threading import Event

    gate, second = Event(), Event()

    class Stalled(Assessments):
        calls = 0

        def citing(
            self, uow: ReadSnapshot | UnitOfWork, episode_id: str, record_ids: Sequence[str]
        ) -> Sequence[str]:
            self.calls += 1
            if self.calls == 1:
                gate.wait(1)
            else:
                second.set()
            return ()

    s = station(tmp_path)
    ep = s.create()
    old = s.answer(ep)
    new = s.answer(ep, supersedes=old)
    s.answer(ep, supersedes=new)
    s.service.providers.assessments = Stalled()
    s.service.limits = replace(s.service.limits, effect_call_timeout_ms=20, effect_batch_size=2)
    worker = RunCorrectionEffects(s.service, s.db, s.db, MemoryHealth(maximum=8))
    try:
        assert worker.run() == 1
        gate.set()
        assert not second.wait(0.1)
        with s.db.snapshot() as view:
            assert {
                row[0] for row in view.connection.rows("SELECT attempts FROM episodes_effect", (), limit=2)
            } == {0, 1}
    finally:
        gate.set()
        worker.close()
