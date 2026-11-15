import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from crew_health.app.assembly import Runtime, TaskHttpServices
from crew_health.app.registry import ModuleRegistry
from crew_health.clock.application.clock import PersistentClocks
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.job_schema import Job
from crew_health.events.adapters.repository import EventRepository
from crew_health.kernel.clock import ClockCorrection, ClockSegment
from crew_health.kernel.commands import command_identity
from crew_health.kernel.task_results import TaskFailureResults, job_audience
from crew_health.kernel.tasks import (
    JobQueries,
    TaskInput,
    TaskQueue,
    TaskRegistration,
    TaskResult,
    TaskRunner,
)
from crew_health.kernel.testing import MemoryJobProjector, MemoryTaskHandler
from crew_health.platform.application.tasks import TaskService

from .test_composition import Station, correct_hub_clock, station


@pytest.mark.trace("SYSTEM.5.10.1", "R17")
def test_composed_task_services_resolve_under_the_canonical_consumer_keys(
    tmp_path: Path, station_resources: Path
) -> None:
    runtime = cast(Runtime, station(tmp_path, station_resources).app().state.runtime)
    assert runtime.serving and runtime.resolver is not None
    service = runtime.resolver.require(TaskService)
    assert runtime.resolver.require(TaskQueue) is service  # type: ignore[type-abstract]
    assert runtime.resolver.require(TaskRunner) is service  # type: ignore[type-abstract]
    assert runtime.resolver.require(JobQueries) is service  # type: ignore[type-abstract]
    assert runtime.resolver.require(TaskFailureResults) is service  # type: ignore[type-abstract]
    assert runtime.resolver.require(TaskHttpServices).queries is service
    runtime.shutdown()


@pytest.mark.trace("SYSTEM.5.10.1", "R17", "V26")
@pytest.mark.parametrize("sensor_correction", [False, True])
def test_public_assessment_job_uses_actual_composition_http_and_outbox(
    tmp_path: Path, station_resources: Path, sensor_correction: bool
) -> None:
    hub = station(tmp_path, station_resources)
    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "assessment_one", "a" * 64),
        lambda uow, lease, result: None,
        lambda result: None,
    )

    def configure(registry: ModuleRegistry) -> None:
        hub.configure(registry)
        registry.task(
            TaskRegistration(
                "public_assessment",
                "v1",
                handler,
                MemoryJobProjector(lambda view, session, result: None),
                "assessment",
            )
        )

    app = Station(hub.db, hub.sessions, configure, hub.resources).app()
    runtime = cast(Runtime, app.state.runtime)
    assert runtime.serving, runtime.fault_code
    assert runtime.resolver is not None
    correct_hub_clock(runtime)
    if sensor_correction:
        clocks = runtime.resolver.require(PersistentClocks)
        with hub.db.begin(actor=hub.sessions.editor.actor, correlation_id="correct_sensor") as uow:
            clocks.open(uow, ClockSegment("clock_sensor", "physical_sensor", "sensor_segment", None, 1, 0))
            clocks.apply(
                uow,
                ClockCorrection(
                    "correction_sensor",
                    "clock_sensor",
                    "sensor_segment",
                    2,
                    uow.now,
                    None,
                    0,
                    10,
                    uow.now,
                    "operator",
                ),
            )
    service = runtime.resolver.require(TaskService)
    actor = hub.sessions.crew.actor
    command = command_identity(
        key="command_assessment",
        actor_ref=actor.id,
        operation="assessment",
        target="episode_one",
        parameters_json=b"{}",
        body_fingerprint="a" * 64,
        fingerprint_kind="sha256",
    )
    with hub.db.snapshot() as view:
        release_id = view.release.release_id
    task = TaskInput(
        "task_assessment",
        "public_assessment",
        "v1",
        "input_assessment",
        "a" * 64,
        release_id,
        None,
        actor,
        job_audience("task_assessment"),
        command,
        "assessment",
    )
    with hub.db.begin(actor=actor, correlation_id="submit_assessment") as uow:
        assert service.submit(uow, task).state == "queued"
        (lease,) = service.claim(
            uow, worker_id="worker_assessment", handlers=frozenset({"public_assessment"}), limit=1
        )
    with hub.db.begin(actor=actor, correlation_id="fail_assessment") as uow:
        assert service.fail_with_result(uow, lease, handler.execute(lease), code=ErrorCode.MODEL_UNAVAILABLE)
    with hub.db.snapshot() as view:
        events = tuple(
            event
            for event in runtime.resolver.require(EventRepository).scan(view.connection, after=0, limit=50)
            if event.type.value == "job.progress"
        )
        assert len(events) == 3
        payload = json.loads(events[-1].data_json)
        assert Job.model_validate(payload).state == "failed"
        assert payload["result_ref"] is None

    async def read() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://hub.test") as client:
            response = await client.get("/v1/jobs/task_assessment", headers={"Authorization": "Bearer crew"})
            assert response.status_code == 200, response.text
            result = Job.model_validate_json(response.content)
            assert result.state == "failed" and result.result_ref is None
            denied = await client.get("/v1/jobs/task_assessment", headers={"Authorization": "Bearer editor"})
            assert denied.status_code == 403

    asyncio.run(read())
    runtime.shutdown()
