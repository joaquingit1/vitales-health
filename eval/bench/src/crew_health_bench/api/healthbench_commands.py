from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import sleep
from uuid import uuid7

import httpx
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import KernelIdFactory
from crew_health.kernel.process_clock import ProcessClock
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.files import bounded_files
from crew_health_bench.adapters.grader_http import GraderHTTP
from crew_health_bench.adapters.healthbench import HealthBenchCorpus, OfficialGrader, PreparedGrader
from crew_health_bench.adapters.healthbench_corpus import VisibleCorpus, prepare_corpus
from crew_health_bench.adapters.healthbench_grader import HealthBenchCaseGrader
from crew_health_bench.adapters.healthbench_reporter import HealthBenchReporter
from crew_health_bench.adapters.hub_system import scan_bound
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.model_generator import IsolatedModelGenerator
from crew_health_bench.adapters.model_release import load_release
from crew_health_bench.adapters.owner import AgeOwnerGrant
from crew_health_bench.adapters.run_inputs import HealthBenchInputs, dataset
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.benchmark import BenchmarkGenerator
from crew_health_bench.application.lifecycle import RunLifecycle
from crew_health_bench.application.ports import CaseGenerator
from crew_health_bench.application.preregistration import PreRegister
from crew_health_bench.domain.documents import integer, number, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.preregistration import preregistration
from crew_health_bench.domain.sample import eligible_order


@dataclass(frozen=True)
class Options:
    command: str
    root: Path
    corpus: Path
    variant: str | None
    files: tuple[Path, ...]
    manifest: Path | None
    run_id: str | None
    model_release: Path | None
    base_url: str | None
    preregistration: Path | None
    salt: Path | None
    grader_url: str | None
    published: Path | None


def _required[T](value: T | None) -> T:
    if value is None:
        raise BenchError("case_invalid")
    return value


class HealthBenchCommands:
    def __init__(self, config_dir: Path, settings: BenchSettings, options: Options) -> None:
        self.config, self.settings, self.options = config_dir.resolve(), settings, options
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.plan = load_document(self.config / "healthbench-plan.json", max_file_bytes=self.maximum)
        self.plan_sha256 = sha256(canonical_json(self.plan)).hexdigest()
        self.repository = self.config.parents[2]
        self.schema = self.repository / "model/schemas/model-release.schema.json"
        self.ids = KernelIdFactory(new_uuid_hex=lambda: uuid7().hex)
        self.clock = ProcessClock(self.ids.new("epoch"))
        self.max_files = scan_bound(self.repository, max_file_bytes=self.maximum)
        self.grader = object_map(self.plan["grader"])

    def _owner(self) -> AgeOwnerGrant:
        policy = load_document(self.config / "partition-policy.json", max_file_bytes=self.maximum)
        return AgeOwnerGrant(recipients=text_list(object_map(policy["vault"])["recipients"]),
                             environment=os.environ, max_file_bytes=self.maximum)

    def fetch(self) -> dict[str, object]:
        options = self.options
        variant = _required(options.variant)
        if len(options.files) != 1:
            raise BenchError("case_invalid")
        prepare_corpus(options.files[0], dataset(self.plan, variant), variant=variant,
                       root=options.corpus, settings=self.settings)
        return {"variant": variant, "dataset_sha256": dataset(self.plan, variant)["sha256"]}

    def _generator(self, store: FileArtifactStore, run_id: str, variant: str,
                   cases: tuple[str, ...], track: str) -> CaseGenerator:
        corpus = VisibleCorpus(self.options.corpus, variant, dataset(self.plan, variant), self.settings)
        visible = {key: corpus.visible(key) for key in cases}
        source = _required(self.options.model_release)
        release = load_release(source, self.schema, max_file_bytes=self.maximum)
        if track == "system_assisted":
            return BenchmarkGenerator(port=None, cases=visible,
                evidence_release_id=text(object_map(release.projection["evaluated_with"])["evidence_release_id"]),
                deadline=release.turn_timeout, store=store, clock=self.clock, ids=self.ids, run_id=run_id)
        return IsolatedModelGenerator(config_dir=self.config, release_path=source, release=release,
            schema=self.schema, base_url=_required(self.options.base_url), settings=self.settings,
            environment=os.environ, grader_key_env=text(self.grader["api_key_env"]), clock=self.clock,
            ids=self.ids, store=store, run_id=run_id, cases=visible)

    def _generated_plans(self, store: FileArtifactStore) -> tuple[str, ...]:
        if not self.options.root.exists():
            return ()
        result: set[str] = set()
        for path in bounded_files(self.options.root, max_files=self.max_files):
            if path.name != "manifest.json" or not store.read_valid(path.parent.name, "generations"):
                continue
            manifest = read_json(path, max_file_bytes=self.maximum)
            documents = object_map(manifest["bench_documents_sha256"])
            pinned = documents.get("healthbench-plan.json")
            if pinned == sha256((self.config / "healthbench-plan.json").read_bytes()).hexdigest():
                result.add(self.plan_sha256)
        return tuple(sorted(result))

    def preregister(self) -> dict[str, object]:
        owner = self._owner()
        owner.require_owner()
        rows = self.plan["datasets"]
        if not isinstance(rows, list):
            raise BenchError("case_invalid")
        sample = object_map(self.plan["sample"])
        warmup_n = integer(self.plan["warmup_n"])
        variants: dict[str, tuple[str, ...]] = {}
        timings: dict[str, tuple[Quantity, ...]] = {}
        with FileArtifactStore(self.options.root, max_line_bytes=self.maximum, max_records=self.max_files) as store:
            generated = self._generated_plans(store)
            if self.plan_sha256 in generated:
                raise BenchError("already_generated")
            for row in rows:
                variant = text(object_map(row)["variant"])
                corpus = VisibleCorpus(self.options.corpus, variant, dataset(self.plan, variant), self.settings)
                variants[variant] = corpus.prompt_ids
                order = eligible_order(corpus.prompt_ids, seed=text(sample["seed"]),
                                       frame_exclusions=text_list(sample["frame_exclusions"]))
                if warmup_n < 1 or len(order) <= warmup_n:
                    raise BenchError("warmup_failed")
                warmup_id = self.ids.derived("benchrun", self.plan_sha256, variant, "warmup")
                generator = self._generator(store, warmup_id, variant, order[-warmup_n:], "model_only")
                durations: list[Quantity] = []
                for case in order[-warmup_n:]:
                    generation = generator.generate(case, 0)
                    if generation.status != "completed":
                        raise BenchError("warmup_failed")
                    store.append(warmup_id, "generations", generation.record(warmup_id))
                    durations.append(generation.wall)
                timings[variant] = tuple(durations)
            budget = object_map(self.plan["budget"])
            registration = preregistration(self.plan_sha256, variants, timings, seed=text(sample["seed"]),
                frame_exclusions=text_list(sample["frame_exclusions"]), warmup_n=warmup_n,
                budget=Quantity(number(budget["value"]), Unit(text(budget["unit"]))), frozen_by="evaluation owner")
            PreRegister(store, owner).freeze(registration, generated_plan_hashes=generated)
        return {"plan_sha256": self.plan_sha256}

    def _registration(self, variant: str) -> tuple[Path, tuple[str, ...], int]:
        source = self.options.preregistration or self.options.root / "preregistration.json"
        registration = read_json(source, max_file_bytes=self.maximum)
        if registration.get("plan_sha256") != self.plan_sha256:
            raise BenchError("drift")
        row = object_map(object_map(registration["variants"])[variant])
        cases = text_list(row["case_ids"])
        if len(cases) != integer(row["n"]) or not cases:
            raise BenchError("warmup_failed")
        return source, cases, integer(row["N"])

    def _context(self) -> tuple[dict[str, object], HealthBenchInputs, tuple[str, ...], int]:
        options = self.options
        source = (_required(options.manifest) if options.command == "plan"
                  else vault_path(options.root, _required(options.run_id) + "/manifest.json"))
        manifest = read_json(source, max_file_bytes=self.maximum)
        variant = text(manifest["variant"])
        registration, cases, count = self._registration(variant)
        if manifest["track"] not in ("model_only", "system_assisted"):
            raise BenchError("case_invalid")
        inputs = HealthBenchInputs(manifest=source, config_dir=self.config,
            release=_required(options.model_release), schema=self.schema, corpus_root=options.corpus,
            variant=variant, preregistration=registration, settings=self.settings)
        if options.command == "plan":
            manifest = inputs.measure()
            if (manifest["suite"], manifest["partition"]) != ("healthbench", "benchmark"):
                raise BenchError("case_invalid")
            source_data = read_json(source, max_file_bytes=self.maximum)
            if source_data.get("case_set_sha256") != manifest["case_set_sha256"]:
                raise BenchError("case_set_mismatch")
        return manifest, inputs, cases, count

    def _official(self, variant: str, client: GraderHTTP) -> OfficialGrader:
        source = self.options.corpus / (variant + ".jsonl")
        row = dataset(self.plan, variant)
        corpus = HealthBenchCorpus(source, text(row["sha256"]), integer(row["bytes"]), integer(row["examples"]), self.settings)
        release = load_release(_required(self.options.model_release), self.schema, max_file_bytes=self.maximum)
        prepared = PreparedGrader(release.model_id, text(self.grader["model"]), client, self.settings)
        return OfficialGrader(corpus, prepared, self.config / "healthbench-plan.json")

    def run(self) -> dict[str, object]:
        manifest, inputs, cases, count = self._context()
        options = self.options
        with FileArtifactStore(options.root, max_line_bytes=self.maximum, max_records=self.max_files) as store:
            lifecycle = RunLifecycle(store=store, inputs=inputs, clock=self.clock, ids=self.ids, manifest=manifest, cases=cases)
            if options.run_id is not None and options.run_id != lifecycle.run_id:
                raise BenchError("drift")
            if options.command == "plan":
                lifecycle.plan()
            elif options.command == "generate":
                budget = object_map(self.plan["budget"])
                lifecycle.generate(self._generator(store, lifecycle.run_id, text(manifest["variant"]), cases,
                                                    text(manifest["track"])),
                                   budget=Quantity(number(budget["value"]), Unit(text(budget["unit"]))))
            else:
                self._grade_or_report(lifecycle, store, cases, count)
            return {"run_id": lifecycle.run_id, "state": lifecycle.state()}

    def _grade_or_report(self, lifecycle: RunLifecycle, store: FileArtifactStore,
                         cases: tuple[str, ...], count: int) -> None:
        options = self.options
        with httpx.Client(trust_env=False) as transport:
            client = GraderHTTP(client=transport, base_url=_required(options.grader_url), grader=self.grader,
                environment=os.environ, settings=self.settings, sleep=lambda q: sleep(float(q.value)))
            manifest = read_json(vault_path(options.root, lifecycle.run_id + "/manifest.json"), max_file_bytes=self.maximum)
            official = self._official(text(manifest["variant"]), client)
            if options.command == "grade":
                lifecycle.grade(HealthBenchCaseGrader(official=official, client=client, store=store,
                    root=options.root, run_id=lifecycle.run_id, max_file_bytes=self.maximum))
                return
            owner = self._owner()
            salt = owner.salt(_required(options.salt), repository=self.repository)
            policy = load_document(self.config / "claims-policy.json", max_file_bytes=self.maximum)
            corpus = official.corpus
            canaries = tuple(sorted({text(corpus.case(key).canary) for key in corpus.prompt_ids
                                     if corpus.case(key).canary is not None}))
            key = os.environ.get(text(self.grader["api_key_env"]))
            if key:
                canaries += (key,)
            reporter = HealthBenchReporter(official=official, store=store, cases=cases, N=count,
                case_salt=salt, canaries=canaries, policy=policy, plan=self.plan,
                validator=ArtifactSchemas(self.config.parent / "schemas", self.maximum))
            with FileArtifactStore(_required(options.published), max_line_bytes=self.maximum,
                                   max_records=self.max_files) as published:
                lifecycle.report(reporter, published)
