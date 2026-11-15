from hashlib import sha256
from pathlib import Path

from crew_health.kernel.commands import canonical_json
from simple_evals import healthbench_eval

from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.files import bounded_files
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


class SuiteInputs:
    """Every input that can change a product-suite result (design 3.5, R22), measured from disk and the
    harness.

    `harness_version` is measured when the harness is resolved (plan, generate); grading and reporting do not
    start a hub, so they keep the version the manifest recorded and every other input is measured again.
    """

    def __init__(
        self,
        *,
        manifest: Path,
        config_dir: Path,
        release: Path,
        schema: Path,
        case_files: tuple[Path, ...],
        release_config: Path,
        harness_version: str | None,
        settings: BenchSettings,
    ) -> None:
        self.manifest, self.config_dir, self.release, self.schema = manifest, config_dir, release, schema
        self.case_files, self.release_config, self.harness_version = (
            case_files,
            release_config,
            harness_version,
        )
        self.settings = settings
        self.maximum = settings.integer("bench.store.max_line_bytes")
        self.max_files = settings.integer("bench.scan.max_files")

    def _release_files(self) -> tuple[Path, ...]:
        files = bounded_files(self.release_config, max_files=self.max_files)
        return tuple(
            path
            for path in files
            if path.parent == self.release_config.absolute() and path.name.endswith(".json")
        )

    def documents(self) -> dict[str, object]:
        """The evaluation release documents by name, for case preconditions (design 3.2, Q19)."""
        return {
            path.name.removesuffix(".json"): read_json(path, max_file_bytes=self.maximum)
            for path in self._release_files()
        }

    def measure(self) -> dict[str, object]:
        manifest = read_json(self.manifest, max_file_bytes=self.maximum)
        release = load_release(self.release, self.schema, max_file_bytes=self.maximum)
        harness = (
            object_map(manifest.get("harness"))
            if self.harness_version is None
            else {"repository": "eval/runner (crew_health_harness)", "commit": self.harness_version}
        )
        release_config = {path.name: _hash(path, self.maximum) for path in self._release_files()}
        manifest.update(
            model_release=dict(release.projection),
            decoding=release.projection["decoding"],
            harness=harness,
            grader={"model": "deterministic typed checks", "version": "suites.json"},
            integrity="official",
            dataset_sha256={
                "cases": sha256(
                    "".join(sorted(_hash(path, self.maximum) for path in self.case_files)).encode("ascii")
                ).hexdigest(),
                "release_config": sha256(canonical_json(release_config)).hexdigest(),
            },
            preregistration_sha256="none:product suites are not preregistered",
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
