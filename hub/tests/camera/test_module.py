"""Registration, binding and the provided ports of the camera module (SYSTEM 5, coordinator decisions A1 and A7)."""

from __future__ import annotations

import dataclasses
import inspect
import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from camera_harness import (
    ADAPTER,
    CAMERA_SETTINGS,
    PROTOCOL,
    START,
    Mission,
    Reports,
    ScriptedTransport,
    Store,
    camera_settings,
    camera_store,
    changed,
    describe,
    kernel_settings,
    memory_tasks,
)

from crew_health.app.registry import DeclarationRegistry
from crew_health.camera import module
from crew_health.camera.adapters.http_transport import AbsentTransport
from crew_health.camera.application import ports as camera_ports
from crew_health.camera.application.ports import (
    AdapterAssignmentPort,
    AdapterReadinessPort,
    CameraReferencePort,
    ReferenceWindowRequest,
    WorkerTransport,
    WorkerUnreachable,
)
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import WorkerLifecycle
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import TaskQueue, TaskRunner
from crew_health.kernel.testing import MemoryUnits
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.measurement.application import ports as measurement_ports
from crew_health.teach.application import ports as teach_ports

pytestmark = pytest.mark.trace("A1", "A7", "V11", "AGENTS-1.3", "AGENTS-6.bounds")
WORKER_SETTINGS = (
    Path(__file__).resolve().parents[3] / "workers/camera/src/crew_health_camera/policy/worker-settings.json"
)


def _registered() -> tuple[DeclarationRegistry, module.Live]:
    registry = DeclarationRegistry(max_ports=16, max_hooks=64)
    live = module.Live()
    module.register(registry, live)
    return registry, live


def _subscriber(registry: DeclarationRegistry) -> tuple[Any, Any]:
    types, handler = cast(tuple[Any, Any], registry.declarations["durable_subscribers"][module.SUBSCRIBER])
    return types, handler


def _port(registry: DeclarationRegistry, port: type[Any]) -> Any:
    return registry.providers[port][0](None)  # type: ignore[arg-type]


def test_register_declares_bounded_settings_with_basis_prefixes_subscriber_and_ports() -> None:
    registry, _ = _registered()
    settings = registry.declarations["settings"]
    for spec in settings.values():
        assert spec.basis  # type: ignore[attr-defined]
        if spec.kind == "integer":  # type: ignore[attr-defined]
            assert spec.maximum is not None and spec.maximum.is_finite()  # type: ignore[attr-defined]
    assert {
        "workers.camera.timeout_ms",
        "workers.camera.health_ms",
        "workers.camera.stale_ms",
        "workers.camera.dispatch_deadline_ms",
        "workers.camera.warmup_max_ms",
        "workers.camera.enabled",
    } <= settings.keys()
    assert all(str(k).startswith("workers.camera.") for k in settings)  # DECISIONS A7
    e20 = (
        "restart_max",
        "restart_window_ms",
        "backoff_max_ms",
        "rss_mb",
        "cpus",
        "stop_grace_ms",
        "stable_run_ms",
    )
    assert not any(str(k).rsplit(".", 1)[-1] in e20 for k in settings)  # E20's supervision keys, never twice
    worker = json.loads(WORKER_SETTINGS.read_text(encoding="utf-8"))
    ttl = settings["workers.camera.acquisition_ttl_ms"]
    assert ttl.maximum <= worker["max_ttl_ms"]["value"]  # type: ignore[attr-defined]  # m7
    warmup = settings["workers.camera.warmup_max_ms"]
    accepted = PROTOCOL["models"]["StartRequest"]["properties"]["warmup_max_ms"]
    assert (warmup.minimum, warmup.maximum) == (accepted["minimum"], accepted["maximum"])  # type: ignore[attr-defined]
    assert "5 s" in settings["workers.camera.health_ms"].basis  # type: ignore[attr-defined]  # SYSTEM 3 camera row
    assert set(registry.declarations["prefixes"]) == {"cad", "crf", "ctk"}
    assert set(registry.declarations["tasks"]) == {"camera.start", "camera.cancel", "camera.report"}  # M6
    types, _ = _subscriber(registry)
    assert types == frozenset({EventType.MEASUREMENT_REQUEST_CHANGED})
    for port in (AdapterAssignmentPort, AdapterReadinessPort, CameraReferencePort):
        assert port in registry.providers


def test_unbound_ports_fail_closed() -> None:
    registry, _ = _registered()
    assert _port(registry, AdapterReadinessPort).ready_methods(None) is None
    assert (
        _port(registry, AdapterAssignmentPort).assign(None, capability_code="pulse", methods=("x",)) is None
    )
    reference = _port(registry, CameraReferencePort)
    request = ReferenceWindowRequest(
        "gck_1",
        1,
        "per_1",
        "pulse",
        START,
        START + timedelta(seconds=30),
        "LIVE",
        False,
        timedelta(minutes=1),
    )
    ticket = reference.begin_window(request, timeout=timedelta(seconds=1))
    assert (ticket.ticket_id, ticket.unavailable_reason) == (None, "camera_unavailable")
    assert reference.outcome("crf_1", wait=timedelta(0)).kind == "unavailable"
    reference.abandon("crf_1", timeout=timedelta(0))
    _, handler = _subscriber(registry)
    with pytest.raises(ProblemError) as caught:
        handler(None, changed("cor_1"))
    assert (
        caught.value.code is ErrorCode.INTERNAL_ERROR and caught.value.retryable
    )  # the cursor never advances


def _bound(
    tmp_path: Path,
    transport: object | None,
    *,
    enabled: bool = False,
    texts: dict[str, str] | None = None,
    tasks: bool = True,
    lifecycle: object | None = None,
) -> tuple[DeclarationRegistry, module.Live, Store]:
    store = camera_store(tmp_path)
    settings = camera_settings(kernel_settings(), enabled=enabled, texts=texts)
    registry, live = _registered()
    values: list[tuple[type[Any], object]] = [
        (HubSettings, settings),
        (Clock, store.clock),
        (IdFactory, store.ids),
        (UnitOfWorkFactory, store.uows),
        (ReadSnapshotFactory, store.uows),
        (UnitRegistry, MemoryUnits({"/min": ("frequency", Decimal(1), Decimal(0))})),
        (camera_ports.MissionStatePort, Mission("SIM")),
    ]
    if tasks:
        queue = memory_tasks(settings, list(registry.declarations["tasks"].values()))  # type: ignore[arg-type]
        values += [(TaskQueue, queue), (TaskRunner, queue), (camera_ports.AttemptReports, Reports())]
    if transport is not None:
        values.append((WorkerTransport, transport))
    if lifecycle is not None:
        values.append((WorkerLifecycle, lifecycle))
    for port, value in values:
        registry.provide(port, _const(value))
    registry.resolve()
    return registry, live, store


def _const(value: object) -> Callable[[object], object]:
    return lambda _resolver: value


def test_binding_wires_the_providers_to_one_link_and_the_loop_probes_then_dispatches(tmp_path: Path) -> None:
    transport = ScriptedTransport([describe()], fallback=describe())
    registry, live, store = _bound(tmp_path, transport)
    readiness = _port(registry, AdapterReadinessPort)
    with store.uows.snapshot() as view:
        assert readiness.ready_methods(view) is None  # not probed yet: unknown
    assert live.loop is not None and live.loop.tick() == 0
    with store.uows.snapshot() as view:
        assert readiness.ready_methods(view) == frozenset({"camera_estimate"})
    assignment = _port(registry, AdapterAssignmentPort)
    with store.uows.begin(actor=Actor("person", "mara"), correlation_id="cor_start") as uow:
        assigned = assignment.assign(uow, capability_code="pulse", methods=("camera_estimate",))
    assert (assigned.adapter_id, assigned.device_id) == (ADAPTER, None)
    _, handler = _subscriber(registry)
    with store.uows.begin(actor=Actor("engine", "events"), correlation_id="cor_delivery") as uow:
        handler(uow, changed("cor_start"))
    transport.steps.append(describe())
    transport.fallback = None
    assert live.loop.tick() == 1  # the bound start was due; the worker did not answer it
    assert [p for _, p, _, _ in transport.calls][-1] == "/v1/acquisitions"


def test_an_enabled_camera_without_a_readable_token_stays_unknown(tmp_path: Path) -> None:
    texts = {
        "workers.camera.url": "http://127.0.0.1:9",
        "workers.camera.token_file": str(tmp_path / "missing-token"),
    }
    registry, live, store = _bound(tmp_path, None, enabled=True, texts=texts)
    assert live.loop is not None
    # The binding started the loop thread, which shares the store's one SQLite writer: join it before ticking here,
    # or a pass still running on that thread collides with this one ("cannot start a transaction within a transaction").
    assert live.loop.stop_within(5.0)
    assert live.loop.tick() == 0
    with store.uows.snapshot() as view:
        assert _port(registry, AdapterReadinessPort).ready_methods(view) is None


class Lifecycle:
    def __init__(self) -> None:
        self.started_workers: list[tuple[str, Callable[[float], bool]]] = []

    def started(self, name: str, stop: Callable[[float], bool]) -> None:
        self.started_workers.append((name, stop))


def test_an_enabled_camera_reports_its_loop_to_the_hub_and_stops_with_it(tmp_path: Path) -> None:
    texts = {
        "workers.camera.url": "http://127.0.0.1:9",
        "workers.camera.token_file": str(tmp_path / "missing-token"),
    }
    lifecycle = Lifecycle()
    _, live, _ = _bound(tmp_path, None, enabled=True, texts=texts, lifecycle=lifecycle)
    assert [name for name, _ in lifecycle.started_workers] == ["camera"]
    _, stop = lifecycle.started_workers[0]
    assert stop(5.0)  # SYSTEM 5.12: the loop's thread ends within the shutdown budget
    assert live.loop is not None and live.loop.stop_within(0.0)


def test_a_disabled_camera_reports_no_worker(tmp_path: Path) -> None:
    lifecycle = Lifecycle()
    _bound(tmp_path, ScriptedTransport(fallback=describe()), lifecycle=lifecycle)
    assert lifecycle.started_workers == []


@pytest.mark.trace("M6", "AGENTS-1.3")
def test_without_e01_tasks_the_camera_is_never_assigned_and_manual_entry_stays(tmp_path: Path) -> None:
    registry, live, store = _bound(tmp_path, ScriptedTransport(fallback=describe()), tasks=False)
    assert live.loop is None and live.tasks is None
    with store.uows.begin(actor=Actor("person", "mara"), correlation_id="cor_start") as uow:
        assignment = _port(registry, AdapterAssignmentPort)
        assert assignment.assign(uow, capability_code="pulse", methods=("camera_estimate",)) is None


def test_a_disabled_camera_is_never_contacted(tmp_path: Path) -> None:
    registry, live, store = _bound(tmp_path, None)
    assert live.loop is not None
    assert isinstance(live.loop._link._transport, AbsentTransport)  # noqa: SLF001
    live.loop.tick()
    with store.uows.snapshot() as view:
        assert _port(registry, AdapterReadinessPort).ready_methods(view) is None


def _shape(value: object) -> object:
    if dataclasses.is_dataclass(value):
        return [(f.name, f.type) for f in dataclasses.fields(value)]
    methods = [n for n, _ in inspect.getmembers(value, inspect.isfunction) if not n.startswith("_")]
    return [(n, str(inspect.signature(getattr(value, n)))) for n in sorted(methods)]


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (camera_ports.AdapterReadinessPort, measurement_ports.AdapterReadinessPort),
        (camera_ports.AdapterAssignmentPort, measurement_ports.AdapterAssignmentPort),
        (camera_ports.AdapterAssignment, measurement_ports.AdapterAssignment),
        (camera_ports.CameraReferencePort, teach_ports.CameraReferencePort),
        (camera_ports.ReferenceWindowRequest, teach_ports.ReferenceWindowRequest),
        (camera_ports.ReferenceTicket, teach_ports.ReferenceTicket),
        (camera_ports.ReferenceOutcome, teach_ports.ReferenceOutcome),
        (camera_ports.MissionFacts, measurement_ports.MissionFacts),
        (camera_ports.MissionStatePort, measurement_ports.MissionStatePort),
    ],
)
def test_structural_copies_match_the_consumers_declarations(ours: type[Any], theirs: type[Any]) -> None:
    """A1: our copies keep main's names and parameters; composition binds them to the consumers' classes (F1)."""
    assert ours.__name__ == theirs.__name__
    assert _shape(ours) == _shape(theirs)
    assert _shape(ours)  # never vacuous


@pytest.mark.trace("A7", "M8")
def test_the_observation_commands_copy_keeps_e30s_shape_with_the_cameras_method() -> None:
    """DECISIONS A7: structural copy of E30's ObservationCommands until E01's records port is on main; only the
    draft's class (and its one method literal) differ."""
    ours, theirs = camera_ports.ObservationCommands, teach_ports.ObservationCommands
    for name in ("canonical_unit", "append_observation"):
        a, b = inspect.signature(getattr(ours, name)), inspect.signature(getattr(theirs, name))
        assert [(p.name, p.kind) for p in a.parameters.values()] == [
            (p.name, p.kind) for p in b.parameters.values()
        ]
        assert a.return_annotation == b.return_annotation
    mine = {f.name: f.type for f in dataclasses.fields(camera_ports.CameraObservationDraft)}
    e30 = {f.name: f.type for f in dataclasses.fields(teach_ports.ObservationDraft)}
    assert mine.keys() == e30.keys()
    assert {k for k in mine if mine[k] != e30[k]} == {"acquisition_method"}


def test_the_providers_satisfy_the_consumers_protocols_structurally() -> None:
    registry, _ = _registered()
    for ours, theirs in (
        (AdapterReadinessPort, measurement_ports.AdapterReadinessPort),
        (AdapterAssignmentPort, measurement_ports.AdapterAssignmentPort),
        (CameraReferencePort, teach_ports.CameraReferencePort),
    ):
        provider = _port(registry, ours)
        for name, _ in inspect.getmembers(theirs, inspect.isfunction):
            if not name.startswith("_"):
                expected = inspect.signature(getattr(theirs, name))
                assert str(inspect.signature(getattr(provider, name))) == str(
                    expected.replace(parameters=list(expected.parameters.values())[1:])
                )


def test_a_failed_loop_pass_is_logged_loudly_without_record_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _, live, _ = _bound(tmp_path, ScriptedTransport())
    loop = live.loop
    assert loop is not None
    passes: list[int] = []

    def failing_tick() -> int:
        passes.append(1)
        loop.stop()  # one pass: the stop event also ends the wait at once, no real time passes
        raise RuntimeError("storage unavailable: mara's private text")

    loop.tick = failing_tick  # type: ignore[method-assign]
    with caplog.at_level("ERROR", logger="crew_health.camera"):
        loop._run()  # noqa: SLF001
    assert len(passes) == 1
    assert [r.getMessage() for r in caplog.records] == [
        "camera link pass failed: RuntimeError"
    ]  # no record text


def test_the_loop_probes_until_the_worker_first_answers_instead_of_waiting_one_health_interval(
    tmp_path: Path,
) -> None:
    """Fix for the camera reading unavailable for one health interval after serve: the worker starts after the hub,
    so the startup probes repeat every workers.camera.startup_probe_ms until it answers, then stop."""
    transport = ScriptedTransport([WorkerUnreachable("refused"), WorkerUnreachable("refused"), describe()])
    registry, live, store = _bound(tmp_path, transport)
    readiness = _port(registry, AdapterReadinessPort)
    loop = live.loop
    assert loop is not None
    loop._await_worker()  # noqa: SLF001
    assert [p for _, p, _, _ in transport.calls] == ["/v1/describe"] * 3  # stopped at the first answer
    with store.uows.snapshot() as view:
        assert readiness.ready_methods(view) == frozenset({"camera_estimate"})


def test_the_startup_probes_are_bounded_when_the_worker_never_answers(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    registry, live, store = _bound(tmp_path, transport)
    loop = live.loop
    assert loop is not None
    loop._startup = 0.0  # noqa: SLF001  # no real waiting; the bound is the probe count
    loop._await_worker()  # noqa: SLF001
    assert (
        len(transport.calls)
        == CAMERA_SETTINGS["workers.camera.stale_ms"] // CAMERA_SETTINGS["workers.camera.startup_probe_ms"]
    )
    with store.uows.snapshot() as view:
        assert (
            _port(registry, AdapterReadinessPort).ready_methods(view) is None
        )  # never ready without an answer


def test_health_states_follow_the_probe_and_a_disabled_camera_lists_nothing(tmp_path: Path) -> None:
    from crew_health.camera.application.ports import AdapterState, AdapterStatesPort

    transport = ScriptedTransport()  # the worker does not answer yet
    (tmp_path / "t").write_text("token", encoding="utf-8")
    texts = {"workers.camera.url": "http://127.0.0.1:9", "workers.camera.token_file": str(tmp_path / "t")}
    registry, live, store = _bound(tmp_path, transport, enabled=True, texts=texts)
    states = _port(registry, AdapterStatesPort)
    try:
        assert live.link is not None
        live.link.probe()
        with store.uows.snapshot() as view:
            assert states.adapter_states(view.now) == (AdapterState(ADAPTER, "disconnected"),)
        transport.fallback = describe()
        live.link.probe()
        with store.uows.snapshot() as view:
            assert states.adapter_states(view.now) == (AdapterState(ADAPTER, "connected"),)
    finally:
        assert live.loop is not None and live.loop.stop_within(5.0)
    (tmp_path / "off").mkdir()
    disabled, _, _ = _bound(tmp_path / "off", ScriptedTransport())
    with store.uows.snapshot() as view:
        assert _port(disabled, AdapterStatesPort).adapter_states(view.now) == ()
