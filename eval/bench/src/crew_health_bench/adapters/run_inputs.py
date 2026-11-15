from hashlib import sha256
from pathlib import Path

from simple_evals import healthbench_eval

from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.healthbench_corpus import VisibleCorpus
from crew_health_bench.adapters.model_release import load_release
from crew_health_bench.adapters.official_source import integrity, official_source
from crew_health_bench.domain.documents import object_map
from crew_health_bench.domain.errors import BenchError

BENCH_DOCUMENTS = (
    "bench-settings.json",
    "claims-policy.json",
    "healthbench-plan.json",
    "partition-policy.json",
    "suites.json",
)


class HealthBenchInputs:
    def __init__(
        self,
        *,
        manifest: Path,
        config_dir: Path,
        release: Path,
        schema: Path,
        corpus_root: Path,
        variant: str,
        preregistration: Path,
        settings: BenchSettings,
    ) -> None:
        self.manifest, self.config_dir, self.release, self.schema = manifest, config_dir, release, schema
        self.corpus_root, self.variant, self.preregistration = corpus_root, variant, preregistration
        self.settings = settings
        self.maximum = settings.integer("bench.store.max_line_bytes")

    def measure(self) -> dict[str, object]:
        manifest = read_json(self.manifest, max_file_bytes=self.maximum)
        plan = load_document(self.config_dir / "healthbench-plan.json", max_file_bytes=self.maximum)
        corpus = VisibleCorpus(self.corpus_root, self.variant, dataset(plan, self.variant), self.settings)
        harness = dict(object_map(plan["harness"]))
        harness["module_sha256"] = _hash(Path(healthbench_eval.__file__), self.maximum)
        release = load_release(self.release, self.schema, max_file_bytes=self.maximum)
        source = official_source(self.config_dir.parent / "vendor", max_file_bytes=self.maximum)
        manifest.update(
            model_release=dict(release.projection),
            decoding=release.projection["decoding"],
            harness=harness,
            grader=plan["grader"],
            integrity=integrity(plan, source, str(harness["module_sha256"])),
            dataset_sha256={self.variant: dataset(plan, self.variant)["sha256"], "visible": corpus.sha256},
            preregistration_sha256=_hash(self.preregistration, self.maximum),
            bench_documents_sha256={
                name: _hash(self.config_dir / name, self.maximum) for name in BENCH_DOCUMENTS
            },
        )
        return manifest

    def verify(self, run_id: str) -> None:
        if not run_id:
            raise BenchError("case_invalid")
        for name in BENCH_DOCUMENTS:
            load_document(self.config_dir / name, max_file_bytes=self.maximum)


def _hash(path: Path, maximum: int) -> str:
    if path.is_symlink() or not path.is_file():
        raise BenchError("release_unreadable")
    try:
        with path.open("rb") as source:
            raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise BenchError("release_unreadable")
        return sha256(raw).hexdigest()
    except OSError as error:
        raise BenchError("target_unreadable", exit_code=6) from error


def dataset(plan: dict[str, object], variant: str) -> dict[str, object]:
    values = plan.get("datasets")
    if not isinstance(values, list):
        raise BenchError("case_invalid")
    matches = [object_map(value) for value in values if object_map(value).get("variant") == variant]
    if len(matches) != 1:
        raise BenchError("case_invalid")
    return matches[0]
