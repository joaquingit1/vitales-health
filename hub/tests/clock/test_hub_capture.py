from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crew_health.clock.adapters.repository import SqlClockRepository
from crew_health.clock.application.clock import PersistentClocks
from crew_health.kernel.clock import ClockCorrected, ClockCorrection, ClockSegment
from crew_health.kernel.testing import MemoryClock, MemoryIds
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-12", "SYSTEM.5.2")]


@pytest.mark.parametrize("offset", [-3600000, 0, 3600000])
def test_hub_capture_retains_the_explicit_mapping_across_other_and_retrospective_corrections(
    tmp_path: Path, offset: int
) -> None:
    clock = MemoryClock(datetime(2026, 11, 15, 12, tzinfo=UTC), 0, "epoch_capture")
    db = database(tmp_path, clock=clock)
    for migration in sorted(Path("hub/src/crew_health/clock/adapters/migrations").glob("*.sql")):
        db.migrate(migration.read_text(encoding="utf-8"))
    repository = SqlClockRepository(
        settings=db.settings, node_id="node_capture", incarnation_id="inc_capture"
    )
    service = PersistentClocks(
        repository=repository,
        settings=db.settings,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(100))),
        hub_source_clock_id="hub_capture",
    )
    actor = Actor("operator", "operator_capture")
    with db.begin(actor=actor, correlation_id="open_capture") as uow:
        segment = service.open_hub_segment(uow, process_epoch=clock.epoch)
    with db.snapshot() as view:
        missing = service.capture_hub(view, origin_mode="SIM")
        assert missing.source_clock_version == 0
        assert service.normalize(view, missing).state == "unknown"
    delivered_capture_versions: list[int] = []

    def observe_mapping(uow: UnitOfWork, event: ClockCorrected) -> None:
        delivered_capture_versions.append(service.capture_hub(uow, origin_mode="SIM").source_clock_version)

    db.bus.subscribe(ClockCorrected, observe_mapping, name="capture_probe")
    with db.begin(actor=actor, correlation_id="confirm_capture") as uow:
        service.confirm_hub_time(uow, correction_id="confirm_one", offset_ms=offset, uncertainty_ms=0)
        service.complete_sweep(uow, correction_version=1)
    assert delivered_capture_versions == [1]
    with db.snapshot() as view:
        captured = service.capture_hub(view, origin_mode="SIM")
        assert captured.original_at == clock.at
        assert captured.source_clock_version == 1
        assert captured.segment_id == segment.segment_id
        assert service.normalize(view, captured).corrected_at == clock.at
    clock.at += timedelta(seconds=1)
    with db.begin(actor=actor, correlation_id="foreign_capture") as uow:
        service.open(uow, ClockSegment("sensor_capture", "physical_sensor", "sensor_segment", None, 1, 0))
        service.apply(
            uow,
            ClockCorrection(
                "sensor_correction",
                "sensor_capture",
                "sensor_segment",
                2,
                uow.now,
                None,
                0,
                0,
                uow.now,
                "operator",
            ),
        )
        # A retrospective correction changes normalization, never which mapping captured a new hub timestamp.
        service.apply(
            uow,
            ClockCorrection(
                "retro_correction",
                segment.source_clock_id,
                segment.segment_id,
                3,
                captured.original_at - timedelta(milliseconds=offset),
                None,
                offset + 100,
                0,
                uow.now,
                "operator",
            ),
        )
    restarted = PersistentClocks(
        repository=repository,
        settings=db.settings,
        ids=MemoryIds(identifiers=()),
        hub_source_clock_id="hub_capture",
    )
    with db.snapshot() as view:
        current = restarted.capture_hub(view, origin_mode="SIM")
        assert current.source_clock_version == 1
        assert restarted.normalize(view, current).corrected_at == clock.at + timedelta(milliseconds=100)
        assert restarted.elapsed(view, captured, current) == timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="rollback"):
        with db.begin(actor=actor, correlation_id="rollback_capture") as uow:
            service.confirm_hub_time(uow, correction_id="confirm_rollback", offset_ms=0, uncertainty_ms=0)
            raise RuntimeError("rollback")
    with db.snapshot() as view:
        assert restarted.capture_hub(view, origin_mode="SIM").source_clock_version == 1
    with db.begin(actor=actor, correlation_id="restart_segment") as uow:
        service.open_hub_segment(uow, process_epoch="epoch_new")
    with db.snapshot() as view:
        assert restarted.capture_hub(view, origin_mode="SIM").source_clock_version == 0
        assert restarted.normalize(view, restarted.capture_hub(view, origin_mode="SIM")).state == "unknown"
