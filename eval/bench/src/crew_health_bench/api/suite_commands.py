"""`chc-bench suite plan | generate | grade | report` (design 4.1, 4.5, 6.2): product suites on evaluation
hubs.

Plan and generate resolve E21's harness through `adapters/providers.py` and refuse with a typed reason while
it, or a hub operation the driver calls, is absent; nothing is planned or recorded then. Generation runs in
this process with no vault identity or grader key (INV-E18-03). Grading opens holdout hidden parts from the
vault in a later process epoch, or reads dev hidden parts from `--hidden-dir`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from uuid import uuid7

from crew_health.kernel.ids import KernelIdFactory
from crew_health.kernel.units import Quantity

from crew_health_bench.adapters import providers
from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.case_documents import CaseDocuments, LoadedCase
from crew_health_bench.adapters.clock import ProcessClock
from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.hub_driver import HubDriver
from crew_health_bench.adapters.isolation import (
    refuse_repository_plaintext,
    refuse_vault_identity,
    vault_path,
)
from crew_health_bench.adapters.owner import AgeOwnerGrant
from crew_health_bench.adapters.partitions import verify_partitions
from crew_health_bench.adapters.run_inputs import SuiteInputs
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.adapters.suite_grader import SuiteGrader
from crew_health_bench.adapters.vault import AgeVault
from crew_health_bench.api.case_commands import load_cases
from crew_health_bench.application.harness_ports import ProductHarness
from crew_health_bench.application.lifecycle import RunLifecycle
from crew_health_bench.application.publication import publishable_artifacts
from crew_health_bench.application.suite import SuiteGenerator
from crew_health_bench.domain.documents import integer, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.readiness import product_suite_readiness
from crew_health_bench.domain.rubrics import CHECK_CATEGORIES
from crew_health_bench.domain.runs import Generation, Grade
from crew_health_bench.domain.visible import ProductCase, VisibleCase


@dataclass(frozen=True)
class SuiteOptions:
    command: str
    root: Path
    cases: tuple[Path, ...]
    manifest: Path | None
    run_id: str | None
    model_release: Path
    release_config: Path
    hidden_dir: Path | None
    salt: Path | None
    published: Path | None


def _required[T](value: T | None) -> T:
    if value is None:
        raise BenchError("case_invalid")
    return value


class SuiteReporter:
    def __init__(
        self,
        *,
        cases: Mapping[str, LoadedCase],
        case_salt: bytes,
        policy: Mapping[str, object],
        validator: ArtifactSchemas,
    ) -> None:
        self.cases, self.salt, self.policy, self.validator = cases, case_salt, policy, validator

    def build(
        self,
        run_id: str,
        manifest: Mapping[str, object],
        generations: tuple[Generation, ...],
        grades: tuple[Grade, ...],
    ) -> Mapping[str, bytes]:
        planned = len(self.cases) * integer(manifest["repeats"])
        return publishable_artifacts(
            evaluation_manifest=manifest,
            run_id=run_id,
            generations=generations,
            grades=grades,
            case_conditions={key: case.case.condition_ids for key, case in self.cases.items()},
            check_categories=CHECK_CATEGORIES,
            completeness="full" if len(generations) == planned else "partial",
            N=planned,
            case_salt=self.salt,
            canaries=tuple(sorted({case.case.canary for case in self.cases.values()})),
            policy=self.policy,
            validator=self.validator,
        )


class SuiteCommands:
    def __init__(self, config_dir: Path, settings: BenchSettings, options: SuiteOptions) -> None:
        self.config, self.settings, self.options = config_dir.resolve(), settings, options
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.max_files = settings.integer("bench.scan.max_files")
        self.repository = self.config.parents[2]
        self.schema = self.repository / "model/schemas/model-release.schema.json"
        self.ids = KernelIdFactory(new_uuid_hex=lambda: uuid7().hex)
        self.clock = ProcessClock(self.ids.new("epoch"))
        self.documents = CaseDocuments.from_config(
            self.config, max_file_bytes=settings.integer("bench.leak.max_file_bytes"), ids=self.ids
        )

    def _harness(self) -> ProductHarness:
        harness = providers.product_harness()
        readiness = product_suite_readiness(harness.mounted_operations(), harness_available=True)
        if not readiness.ready:
            raise BenchError(
                text(readiness.reason),
                exit_code=6,
                detail="evaluation hub lacks: " + ", ".join(readiness.missing_operations),
            )
        return harness

    def _cases(self) -> dict[str, LoadedCase]:
        loaded = load_cases(self.options.cases, documents=self.documents, max_files=self.max_files)
        return {case.case.case_id: case for case in loaded}

    def _require_partition(self, manifest: Mapping[str, object], cases: Mapping[str, LoadedCase]) -> None:
        """Every case must sit in the run's partition of the frozen manifest (INV-E18-01, design 3.4)."""
        root = self.config.parent
        verify_partitions(root, max_file_bytes=self.maximum)
        rows = read_json(root / "partitions/manifest.json", max_file_bytes=self.maximum).get("partitions")
        if not isinstance(rows, list):
            raise BenchError("case_invalid")
        frozen = {text(object_map(row)["case_id"]): text(object_map(row)["partition"]) for row in rows}
        if manifest["partition"] not in ("dev", "holdout") or any(
            frozen.get(case_id) != manifest["partition"] for case_id in cases
        ):
            raise BenchError("partition_mismatch")

    def _lifecycle(
        self, store: FileArtifactStore, harness: ProductHarness | None
    ) -> tuple[RunLifecycle, dict[str, LoadedCase], dict[str, object], SuiteInputs]:
        options = self.options
        source = (
            _required(options.manifest)
            if options.command == "plan"
            else vault_path(options.root, _required(options.run_id) + "/manifest.json")
        )
        cases = self._cases()
        inputs = SuiteInputs(
            manifest=source,
            config_dir=self.config,
            release=options.model_release,
            schema=self.schema,
            case_files=tuple(options.cases),
            release_config=options.release_config,
            harness_version=None if harness is None else harness.version,
            settings=self.settings,
        )
        manifest = inputs.measure()
        if manifest["track"] not in ("rules_only", "system_assisted") or manifest["variant"] is not None:
            raise BenchError("case_invalid")
        self._require_partition(manifest, cases)
        if manifest["partition"] == "holdout":
            refuse_repository_plaintext(tuple(options.cases), repository=self.repository)
        lifecycle = RunLifecycle(
            store=store,
            inputs=inputs,
            clock=self.clock,
            ids=self.ids,
            manifest=manifest,
            cases=tuple(sorted(cases)),
        )
        if options.run_id is not None and options.run_id != lifecycle.run_id:
            raise BenchError("drift")
        return lifecycle, cases, manifest, inputs

    def run(self) -> dict[str, object]:
        command = self.options.command
        harness: ProductHarness | None = None
        if command in ("plan", "generate"):
            if command == "generate":
                refuse_vault_identity(os.environ, grader_key_env=self._grader_key_env())
            harness = self._harness()
        with FileArtifactStore(
            self.options.root, max_line_bytes=self.maximum, max_records=self.max_files
        ) as store:
            lifecycle, cases, manifest, inputs = self._lifecycle(store, harness)
            if command == "plan":
                lifecycle.plan()
            elif command == "generate":
                lifecycle.generate(
                    self._generator(store, _required(harness), lifecycle.run_id, cases, manifest, inputs)
                )
            elif command == "grade":
                lifecycle.grade(self._grader(store, lifecycle.run_id, cases, manifest))
            else:
                self._report(lifecycle, cases)
            return {"run_id": lifecycle.run_id, "state": lifecycle.state()}

    def _grader_key_env(self) -> str:
        plan = load_document(self.config / "healthbench-plan.json", max_file_bytes=self.maximum)
        return text(object_map(plan["grader"])["api_key_env"])

    def _generator(
        self,
        store: FileArtifactStore,
        harness: ProductHarness,
        run_id: str,
        cases: Mapping[str, LoadedCase],
        manifest: Mapping[str, object],
        inputs: SuiteInputs,
    ) -> SuiteGenerator:
        driver = HubDriver(
            harness=harness,
            settings=self.settings,
            clock=self.clock,
            ids=self.ids,
            run_id=run_id,
            sleep=lambda quantity: sleep(float(quantity.value) / 1000),
        )
        products = {
            key: ProductCase(
                case.case,
                VisibleCase(
                    key,
                    tuple(step.model_dump(mode="json") for step in case.visible.seed_steps),
                    case.visible.opening_complaint,
                    tuple(step.model_dump(mode="json") for step in case.visible.script),
                    case.case.canary,
                ),
            )
            for key, case in cases.items()
        }
        deadline: Quantity = self.settings.quantity("bench.case.deadline")
        return SuiteGenerator(
            system=driver,
            cases=products,
            track=text(manifest["track"]),
            deadline=deadline,
            documents=inputs.documents(),
            store=store,
            clock=self.clock,
            ids=self.ids,
            run_id=run_id,
        )

    def _grader(
        self,
        store: FileArtifactStore,
        run_id: str,
        cases: Mapping[str, LoadedCase],
        manifest: Mapping[str, object],
    ) -> SuiteGrader:
        if manifest["partition"] == "dev":
            return SuiteGrader(
                root=self.options.root,
                cases=cases,
                documents=self.documents,
                max_file_bytes=self.maximum,
                hidden_dir=_required(self.options.hidden_dir),
            )
        commits = [row for row in store.read_valid(run_id, "state") if row.get("state") == "generated"]
        if len(commits) != 1:
            raise BenchError("invalid_transition")
        vault = AgeVault(
            root=self.repository / "eval/vault",
            repository_root=self.repository,
            store=store,
            max_file_bytes=self.maximum,
            max_files=max(len(cases), 1),
            environment=os.environ,
            generation_epoch=text(commits[0].get("process_epoch")),
            current_epoch=self.clock.process_epoch(),
        )
        return SuiteGrader(
            root=self.options.root,
            cases=cases,
            documents=self.documents,
            max_file_bytes=self.maximum,
            vault=vault,
        )

    def _report(self, lifecycle: RunLifecycle, cases: Mapping[str, LoadedCase]) -> None:
        policy = load_document(self.config / "partition-policy.json", max_file_bytes=self.maximum)
        owner = AgeOwnerGrant(
            recipients=text_list(object_map(policy["vault"])["recipients"]),
            environment=os.environ,
            max_file_bytes=self.maximum,
        )
        reporter = SuiteReporter(
            cases=cases,
            case_salt=owner.salt(_required(self.options.salt), repository=self.repository),
            policy=load_document(self.config / "claims-policy.json", max_file_bytes=self.maximum),
            validator=ArtifactSchemas(self.config.parent / "schemas", self.maximum),
        )
        with FileArtifactStore(
            _required(self.options.published), max_line_bytes=self.maximum, max_records=self.max_files
        ) as published:
            lifecycle.report(reporter, published)
