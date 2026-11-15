# mypy: disable-error-code="type-abstract"
"""Composition declarations and exact structural seam signatures."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import TypeAliasType, cast, get_args, get_origin, get_type_hints

import pytest

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.clock.module import register as register_clock
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.tasks import TaskRegistration
from crew_health.platform.module import register as register_platform
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.domain.observations import ObservationRecord
from crew_health.records.module import register as register_records
from crew_health.trends import module
from crew_health.trends.application import ports
from crew_health.trends.application.ports import (
    EnvironmentContextPort,
    ForecastCommandsPort,
    ForecastQueriesPort,
    IndicatorSummaryPort,
    ObservationReader,
    ObservationRef,
    SeriesKey,
    SummaryRequest,
)

from .support import catalog_release, person_session
from .test_summary import NOW, Snapshot


@pytest.mark.trace("E14-01", "E14-02", "E14-03", "SYSTEM-5.12")
def test_registration_declares_bounded_settings_prefix_internal_task_check_and_ports() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    module.register(registry)
    assert set(registry.declarations["settings"]) == {
        "trends.history_max_rows",
        "trends.max_codes",
        "trends.environment_points_max",
        "trends.forecast_steps_max",
        "trends.forecast_staging_max",
        "workers.forecast.enabled",
        "workers.forecast.base_url",
        "workers.forecast.token_path",
        "workers.forecast.timeout_ms",
        "workers.forecast.response_max_bytes",
    }
    assert registry.declarations["prefixes"] == {"fci": "forecast_input"}
    assert "trends.indicator_catalog" in registry.declarations["loader_checks"]
    task = registry.declarations["tasks"]["trends.forecast"]
    assert isinstance(task, TaskRegistration)
    assert task.name == "trends.forecast" and task.version == "1"
    assert task.public_kind is None and task.result_projector is None
    assert "trends" in registry.bindings
    assert not registry.declarations.get("routers")
    for port in (IndicatorSummaryPort, EnvironmentContextPort, ForecastCommandsPort, ForecastQueriesPort):
        assert port in registry.providers


@pytest.mark.trace("E14-01", "SYSTEM-5.12", "LESSON-15")
def test_real_records_clock_and_platform_register_alongside_trends() -> None:
    registry = DeclarationRegistry(max_ports=128, max_hooks=512)
    for register in (register_records, register_clock, register_platform, module.register):
        register(registry)
    for port in (IndicatorSummaryPort, EnvironmentContextPort, ForecastCommandsPort, ForecastQueriesPort):
        assert port in registry.providers


@pytest.mark.trace("E14-01", "A1")
def test_observation_reader_signature_names_and_parameter_kinds_match_records_queries() -> None:
    def signature(value: Callable[..., object]) -> tuple[tuple[str, inspect._ParameterKind], ...]:
        return tuple((p.name, p.kind) for p in inspect.signature(value).parameters.values())

    assert signature(ObservationReader.accepted) == signature(RecordsQueries.accepted)


def _resolved(value: object) -> object:
    """Type aliases of the two modules are distinct objects; compare what they stand for."""
    if isinstance(value, TypeAliasType):
        return _resolved(value.__value__)
    arguments = get_args(value)
    if arguments:
        return (get_origin(value), tuple(_resolved(argument) for argument in arguments))
    return value


@pytest.mark.trace("E14-01", "A1", "A9")
def test_observation_ref_members_equal_records_observation_record_fields_and_types() -> None:
    """m8 regression: the drift test compares the returned row too, so records binds without any adapter."""
    record = get_type_hints(ObservationRecord)
    members = {
        name: get_type_hints(getattr(ObservationRef, name).fget)["return"]
        for name, value in vars(ObservationRef).items()
        if isinstance(value, property)
    }
    assert set(members) == {field.name for field in fields(ObservationRecord)}
    assert {name: _resolved(kind) for name, kind in members.items()} == {
        name: _resolved(kind) for name, kind in record.items()
    }
    assert get_type_hints(ObservationReader.accepted)["return"] == tuple[ObservationRef, ...]


def _bind_records(queries: RecordsQueries) -> ObservationReader:
    # Never called: mypy --strict over the tests proves records' query satisfies the structural port as is.
    return queries


@pytest.mark.trace("E14-01", "X-E03-16", "SYSTEM-6.4")
def test_ports_dtos_have_no_direct_or_type_checking_domain_import() -> None:
    assert ports.__file__ is not None
    tree = ast.parse(Path(ports.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("crew_health.trends.domain")
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("crew_health.trends.domain") for alias in node.names)


@pytest.mark.trace("E14-01", "E14-02", "E14-03", "V25")
def test_unbound_facades_fail_closed_before_binding() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    module.register(registry)
    resolver = cast(Resolver, None)
    snapshot = Snapshot(catalog_release())
    session = person_session()
    summary = cast(IndicatorSummaryPort, registry.providers[IndicatorSummaryPort][0](resolver))
    environment = cast(EnvironmentContextPort, registry.providers[EnvironmentContextPort][0](resolver))
    commands = cast(ForecastCommandsPort, registry.providers[ForecastCommandsPort][0](resolver))
    queries = cast(ForecastQueriesPort, registry.providers[ForecastQueriesPort][0](resolver))
    with pytest.raises(ProblemError) as caught:
        summary.summaries(snapshot, session, SummaryRequest("person", "mission", "SIM", ("pulse",)))
    assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE
    with pytest.raises(ProblemError) as caught:
        environment.series(snapshot, session, "channel", until=NOW)
    assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE
    with pytest.raises(ProblemError) as caught:
        queries.latest(snapshot, session, SeriesKey("person", "mission", "SIM", "pulse"))
    assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE
    from crew_health.kernel.uow import UnitOfWork

    with pytest.raises(ProblemError) as caught:
        commands.refresh(cast(UnitOfWork, snapshot), session, SeriesKey("person", "mission", "SIM", "pulse"))
    assert caught.value.code is ErrorCode.CAPABILITY_UNAVAILABLE


@pytest.mark.trace("E14-03", "V69")
@pytest.mark.parametrize(
    ("token_kind", "reason"),
    [
        ("absent", "worker_token_missing"),
        ("empty", "worker_token_missing"),
        ("directory", "worker_token_missing"),
        ("oversized", "worker_token_missing"),
        ("public_address", "worker_address_invalid"),
    ],
)
def test_missing_enabled_worker_token_keeps_module_available_and_returns_reason(
    tmp_path: Path, token_kind: str, reason: str
) -> None:
    """m6 and m2 regressions included: the token read is bounded and a public worker address is refused at bind."""
    from crew_health.kernel.ids import IdFactory
    from crew_health.kernel.met import MissionClock
    from crew_health.kernel.settings import HubSettings
    from crew_health.kernel.uow import ReadSnapshotFactory
    from crew_health.trends.application.ports import IndicatorUnavailable, Unavailable

    from .support import mission_clock, settings, station

    h = station(tmp_path)
    token_path = tmp_path / "token"
    if token_kind == "empty":
        token_path.write_text("  \n", encoding="utf-8")
    elif token_kind == "directory":
        token_path.mkdir()
    elif token_kind == "oversized":
        token_path.write_bytes(b"t" * (module.TOKEN_MAX_BYTES + 1))
    elif token_kind == "public_address":
        token_path.write_text("token", encoding="utf-8")
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    module.register(registry)
    profile = settings(
        **{
            "workers.forecast.enabled": True,
            "workers.forecast.token_path": str(token_path),
        }
        | (
            {"workers.forecast.base_url": "http://worker.example.com:8094"}
            if token_kind == "public_address"
            else {}
        )
    )
    registry.provide(ReadSnapshotFactory, lambda _: h.db)
    registry.provide(HubSettings, lambda _: profile)
    registry.provide(IdFactory, lambda _: h.db.ids)
    registry.provide(MissionClock, lambda _: mission_clock())
    resolver = registry.resolve()
    commands = resolver.require(ForecastCommandsPort)
    with h.db.begin(actor=h.session.actor, correlation_id="refresh") as uow:
        result = commands.refresh(uow, h.session, SeriesKey("person", "mission", "SIM", "pulse"))
    assert result == Unavailable("forecast_worker", reason)
    with h.db.snapshot() as snapshot:
        summaries = resolver.require(IndicatorSummaryPort).summaries(
            snapshot, h.session, SummaryRequest("person", "mission", "SIM", ("pulse",))
        )
        assert summaries == (
            IndicatorUnavailable("pulse", "SIM", Unavailable("E01", "observation_reader_unbound")),
        )
        environment = resolver.require(EnvironmentContextPort).series(
            snapshot, h.session, "channel", until=NOW
        )
        assert environment == Unavailable("E23", "sensor_series_unbound")
