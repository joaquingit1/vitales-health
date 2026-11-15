"""Real SQLite tasks reconnect and retry; missing providers and foreign sessions fail closed.

The E15 EpisodeReader fake is used only when the episode port is present.
No forecast worker fake is installed, including in durability scenarios.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.trends.application.ports import (
    ForecastTicket,
    IndicatorUnavailable,
    SummaryRequest,
    Unavailable,
)

from .support import ENGINE, KEY, NOW, ScenarioHub, history, person_session
from .test_forecast_optional import worker_settings

pytestmark = pytest.mark.trace("V25", "V26", "V35", "V69", "V70", "AGENTS-1.3")


def enabled_hub(path: Path) -> ScenarioHub:
    """Configure the real HTTP adapter; claiming and failing a task makes no worker call."""
    token = path / "worker.token"
    token.write_text("e14-local-durability-token", encoding="utf-8")
    return ScenarioHub(path, overrides=worker_settings("http://127.0.0.1:1", token))


def test_duplicate_refresh_and_terminal_failure_keep_durable_generations(tmp_path: Path) -> None:
    hub = enabled_hub(tmp_path)
    history(hub)
    first = hub.refresh()
    assert isinstance(first, ForecastTicket)
    assert hub.refresh() == replace(first, reconnected=True)
    with hub.station.db.snapshot() as snapshot:
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_input", (), limit=1) == ((1,),)
        assert snapshot.connection.rows("SELECT task_id, state FROM platform_task", (), limit=2) == (
            (first.task_id, "queued"),
        )
    lease = hub.claim()
    assert lease.task.input_ref == first.input_ref
    assert lease.task.task_id == first.task_id
    with hub.station.db.begin(actor=ENGINE, correlation_id="fail") as uow:
        assert hub.tasks.fail(uow, lease, code=ErrorCode.CAPABILITY_UNAVAILABLE, retryable=False)
    again = hub.refresh()
    assert isinstance(again, ForecastTicket)
    assert not again.reconnected
    assert again.generation == first.generation + 1
    assert again.input_ref != first.input_ref
    assert again.task_id != first.task_id
    with hub.station.db.snapshot() as snapshot:
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_input", (), limit=1) == ((2,),)
        states = snapshot.connection.rows("SELECT task_id, state FROM platform_task", (), limit=3)
    assert set(states) == {(first.task_id, "failed"), (again.task_id, "queued")}
    assert hub.latest() == Unavailable("trends", "forecast_pending")


def test_all_optional_providers_absent_still_binds_and_accepts_manual_records(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path, observations=False, episodes=False, queue=False)
    identity = hub.post(14, 45)
    response = hub.client.get(f"/v1/observations/{identity}", headers={"X-Person": "person"})
    assert response.status_code == 200
    with hub.station.db.snapshot() as snapshot:
        assert hub.summary.summaries(
            snapshot,
            person_session(),
            SummaryRequest("person", "mission", "SIM", ("pulse",)),
        ) == (IndicatorUnavailable("pulse", "SIM", Unavailable("E01", "observation_reader_unbound")),)
        assert hub.environment.series(
            snapshot,
            person_session(kind="station"),
            "co2_iss",
            until=NOW,
        ) == Unavailable("E23", "sensor_series_unbound")
    assert hub.refresh() == Unavailable("forecast_worker", "worker_not_configured")
    assert hub.latest() == Unavailable("trends", "no_forecast_requested")


@pytest.mark.parametrize("session", [person_session("another"), person_session(kind="station")])
def test_foreign_person_and_station_cannot_read_or_refresh_private_trends(
    tmp_path: Path,
    session: SessionContext,
) -> None:
    hub = ScenarioHub(tmp_path)
    history(hub)
    with hub.station.db.snapshot() as snapshot:
        with pytest.raises(ProblemError) as summary_denied:
            hub.summary.summaries(snapshot, session, SummaryRequest("person", "mission", "SIM", ("pulse",)))
        with pytest.raises(ProblemError) as forecast_denied:
            hub.forecasts.latest(snapshot, session, KEY)
    with (
        pytest.raises(ProblemError) as refresh_denied,
        hub.station.db.begin(actor=session.actor, correlation_id="denied") as uow,
    ):
        hub.commands.refresh(uow, session, KEY)
    assert summary_denied.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assert forecast_denied.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assert refresh_denied.value.code is ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.parametrize(
    ("observations", "episodes", "queue", "expected"),
    [
        (False, True, True, Unavailable("E01", "observation_reader_unbound")),
        (True, False, True, Unavailable("E15", "episode_periods_unavailable")),
        (True, True, False, Unavailable("task_runner", "task_runner_unbound")),
    ],
)
def test_each_missing_forecast_dependency_has_its_typed_reason(
    tmp_path: Path,
    observations: bool,
    episodes: bool,
    queue: bool,
    expected: Unavailable,
) -> None:
    token = tmp_path / "worker.token"
    token.write_text("e14-local-absence-token", encoding="utf-8")
    hub = ScenarioHub(
        tmp_path,
        observations=observations,
        episodes=episodes,
        queue=queue,
        overrides=worker_settings("http://127.0.0.1:1", token),
    )
    history(hub)
    assert hub.refresh() == expected
    with hub.station.db.snapshot() as snapshot:
        assert snapshot.connection.rows("SELECT COUNT(*) FROM trends_forecast_input", (), limit=1) == ((0,),)
