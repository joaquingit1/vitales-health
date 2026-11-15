from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from crew_health.kernel.ids import KernelIdFactory
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.units import Quantity, Unit
from crew_health_bench.adapters.case_documents import CaseDocuments
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.adapters.suite_grader import SuiteGrader
from crew_health_bench.application.ports import Simulator
from crew_health_bench.application.suite import SuiteGenerator
from crew_health_bench.domain.cases import ConfigPrecondition
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation
from crew_health_bench.domain.trace import CaseTrace
from crew_health_bench.domain.visible import ProductCase, VisibleCase

ROOT = Path(__file__).resolve().parents[1]


class System:
    def __init__(self, trace: CaseTrace) -> None:
        self.trace = trace
        self.calls = 0

    def run_case(
        self, case: VisibleCase, simulator: Simulator, *, track: str, deadline: Quantity
    ) -> CaseTrace:
        self.calls += 1
        assert track == "rules_only"
        assert deadline.unit.code == "s"
        assert simulator.measure("not_in_table").outcome == "unavailable"
        assert case.opening_complaint
        return self.trace


@pytest.mark.trace("R46", "V34", "T-E18-31")
def test_suite_roundtrip_and_missing_precondition_excludes_system_call(tmp_path: Path) -> None:
    ids = KernelIdFactory(new_uuid_hex=lambda: "0" * 32)
    parser = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=ids)
    loaded = parser.load(ROOT / "fixtures/case-document.json")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / (loaded.case.case_id + ".json")).write_bytes((ROOT / "fixtures/hidden-part.json").read_bytes())
    case = ProductCase(
        replace(loaded.case, requires=(ConfigPrecondition("protocol", "/value", "set"),)),
        VisibleCase(loaded.case.case_id, (), loaded.visible.opening_complaint, (), loaded.case.canary),
    )
    system = System(CaseTrace(warnings=(("warning_protocol", "emergency", "rule"),), requests_between=()))
    at = datetime(2026, 11, 14, tzinfo=UTC)
    with FileArtifactStore(tmp_path / "runs", max_line_bytes=1048576, max_records=10) as store:
        documents: dict[str, object] = {"protocol": {"value": None}}
        generator = SuiteGenerator(
            system=system,
            cases={case.case.case_id: case},
            track="rules_only",
            deadline=Quantity(Decimal(1), Unit("s")),
            documents=documents,
            store=store,
            clock=MemoryClock(at, 0, "epoch_generation"),
            ids=ids,
            run_id="benchrun_protocol",
        )
        missing = generator.generate(case.case.case_id, 0)
        assert missing.status == "not_run" and system.calls == 0
        documents["protocol"] = {"value": {"value": 1, "unit": "1"}}
        generation = generator.generate(case.case.case_id, 0)
        assert generation.status == "completed" and generation.model_calls == 0 and system.calls == 1
        grader = SuiteGrader(
            root=tmp_path / "runs",
            cases={case.case.case_id: loaded},
            documents=parser,
            max_file_bytes=1048576,
            hidden_dir=hidden,
        )
        result = grader.grade(generation)
        assert result.outcomes == (("UrgencyAtLeast", "pass", ("warning_protocol",)),)
        assert grader.grade(missing).outcomes == ()


@pytest.mark.trace("R46", "R22", "invariant")
@pytest.mark.parametrize("requests", [None, (1,)])
def test_missing_recorder_evidence_and_rules_track_model_calls_never_pass(
    tmp_path: Path, requests: tuple[int, ...] | None
) -> None:
    ids = KernelIdFactory(new_uuid_hex=lambda: "0" * 32)
    parser = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=ids)
    loaded = parser.load(ROOT / "fixtures/case-document.json")
    case = ProductCase(
        loaded.case,
        VisibleCase(loaded.case.case_id, (), loaded.visible.opening_complaint, (), loaded.case.canary),
    )
    with FileArtifactStore(tmp_path, max_line_bytes=1048576, max_records=10) as store:
        generator = SuiteGenerator(
            system=System(CaseTrace(requests_between=requests)),
            cases={case.case.case_id: case},
            track="rules_only",
            deadline=Quantity(Decimal(1), Unit("s")),
            documents={},
            store=store,
            clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_generation"),
            ids=ids,
            run_id="benchrun_protocol",
        )
        result = generator.generate(case.case.case_id, 0)
        assert result.status == "harness_error"
        assert result.model_calls == (None if requests is None else 1)
        assert Generation.from_record(result.record("benchrun_protocol")) == result


@pytest.mark.trace("E18-03", "R46", "INV-E18-05")
def test_without_harness_or_providers_every_case_is_not_run_with_its_reason(tmp_path: Path) -> None:
    from crew_health.kernel.commands import canonical_json
    from crew_health_bench.domain.readiness import DRIVER_OPERATIONS, product_suite_readiness

    ids = KernelIdFactory(new_uuid_hex=lambda: "0" * 32)
    parser = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=ids)
    loaded = parser.load(ROOT / "fixtures/case-document.json")
    case = ProductCase(
        loaded.case,
        VisibleCase(loaded.case.case_id, (), loaded.visible.opening_complaint, (), loaded.case.canary),
    )
    missing = product_suite_readiness(DRIVER_OPERATIONS[1:], harness_available=True)
    assert (missing.ready, missing.reason, missing.missing_operations) == (
        False,
        "provider_missing",
        DRIVER_OPERATIONS[:1],
    )
    assert product_suite_readiness(DRIVER_OPERATIONS, harness_available=False).reason == "harness_unavailable"
    assert product_suite_readiness(reversed(DRIVER_OPERATIONS), harness_available=True).ready
    with FileArtifactStore(tmp_path, max_line_bytes=1048576, max_records=10) as store:
        with pytest.raises(BenchError, match="case_invalid"):
            SuiteGenerator(
                system=System(CaseTrace(requests_between=())),
                cases={},
                track="rules_only",
                deadline=Quantity(Decimal(1), Unit("s")),
                documents={},
                store=store,
                clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_generation"),
                ids=ids,
                run_id="benchrun_protocol",
                readiness=missing,
            )
        generator = SuiteGenerator(
            system=None,
            cases={case.case.case_id: case},
            track="system_assisted",
            deadline=Quantity(Decimal(1), Unit("s")),
            documents={},
            store=store,
            clock=MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_generation"),
            ids=ids,
            run_id="benchrun_protocol",
            readiness=missing,
        )
        result = generator.generate(case.case.case_id, 0)
        assert result.status == "not_run" and result.model_calls == 0
        output = (tmp_path / result.output_ref).read_bytes()
        assert output == canonical_json(
            {"missing_operations": list(DRIVER_OPERATIONS[:1]), "reason": "provider_missing"}
        )
