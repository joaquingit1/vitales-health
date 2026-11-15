from hashlib import sha256
from pathlib import Path

import httpx
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.units import Quantity

from crew_health_bench.adapters.documents import BenchSettings, decode_json, read_json
from crew_health_bench.adapters.healthbench import HealthBenchCorpus
from crew_health_bench.adapters.isolation import vault_path
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.domain.documents import integer, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import HealthBenchVisible
from crew_health_bench.domain.text import fingerprints


def healthbench_fingerprints(
    corpora: tuple[HealthBenchCorpus, ...], *, fp_salt: str, sample_modulus: int
) -> bytes:
    """Salted 8-gram fingerprints of every HealthBench prompt, rubric line and ideal completion (design 5.4).

    A shingle hash is kept when its value is a multiple of `sample_modulus`, so containment measured
    against a target's full shingle set stays an unbiased estimate while the committed file stays
    small. Examples shared by several variants are fingerprinted once. The case reference is salted;
    no text is written.
    """
    if not fp_salt or sample_modulus < 1 or not corpora:
        raise BenchError("case_invalid")
    rows: dict[str, dict[str, object]] = {}
    for corpus in corpora:
        for key in corpus.prompt_ids:
            reference = "healthbench:" + sha256((fp_salt + key).encode("utf-8")).hexdigest()[:16]
            if reference in rows:
                continue
            case = corpus.case(key)
            ideal = case.ideal_completions_data or {}
            parts = [message.content for message in case.prompt] + [item.criterion for item in case.rubrics]
            parts += [
                value
                for name, value in sorted(ideal.items())
                if name.startswith("ideal_completion") and isinstance(value, str)
            ]
            kept: set[str] = set()
            for part in parts:
                kept |= {
                    value
                    for value in fingerprints(part, fp_salt=fp_salt)
                    if int(value, 16) % sample_modulus == 0
                }
            canaries = [case.canary] if case.canary else []
            if not kept and not canaries:
                continue
            rows[reference] = {"case_ref": reference, "shingles": sorted(kept), "canaries": canaries}
    return canonical_json(
        {
            "fp_salt": fp_salt,
            "sample_modulus": sample_modulus,
            "source": "healthbench",
            "cases": [rows[key] for key in sorted(rows)],
        }
    )


def prepare_corpus(
    source: Path, dataset: dict[str, object], *, variant: str, root: Path, settings: BenchSettings
) -> None:
    size, examples = integer(dataset["bytes"]), integer(dataset["examples"])
    digest = text(dataset["sha256"])
    corpus = HealthBenchCorpus(source, digest, size, examples, settings)
    visible = b"".join(
        canonical_json(
            {
                "prompt_id": case.prompt_id,
                "prompt": [{"role": role, "content": content} for role, content in case.messages],
            }
        )
        + b"\n"
        for case in (corpus.visible(key) for key in corpus.prompt_ids)
    )
    with source.open("rb") as stream:
        original = stream.read(size + 1)
    if len(original) != size or sha256(original).hexdigest() != digest:
        raise BenchError("dataset_hash_mismatch")
    receipt = {
        "variant": variant,
        "dataset_sha256": digest,
        "visible_sha256": sha256(visible).hexdigest(),
        "examples": examples,
    }
    with FileArtifactStore(root, max_line_bytes=size, max_records=examples) as store:
        store.write_atomic(Path(variant + ".jsonl"), original)
        store.write_atomic(Path(variant + ".visible.jsonl"), visible)
        store.write_atomic(Path(variant + ".receipt.json"), canonical_json(receipt))


def download_dataset(
    client: httpx.Client,
    dataset: dict[str, object],
    destination: Path,
    *,
    timeout: Quantity,
    connect_timeout: Quantity,
    chunk_bytes: int,
) -> Path:
    """Stream one pinned dataset file to `destination`; refuse a byte past the pinned size or a wrong hash."""
    size, digest = integer(dataset["bytes"]), text(dataset["sha256"])
    if timeout.unit.code != "s" or connect_timeout.unit.code != "s" or chunk_bytes < 1:
        raise BenchError("case_invalid")
    partial = destination.with_name(destination.name + ".part")
    checksum, written = sha256(), 0
    bounded = httpx.Timeout(float(timeout.value), connect=float(connect_timeout.value))
    try:
        with client.stream("GET", text(dataset["url"]), timeout=bounded, follow_redirects=False) as response:
            if response.status_code != 200:
                raise BenchError("network_unavailable", instance=text(dataset["url"]), exit_code=6)
            with partial.open("wb") as sink:
                for chunk in response.iter_bytes(chunk_size=chunk_bytes):
                    written += len(chunk)
                    if written > size:
                        raise BenchError("dataset_hash_mismatch", instance=text(dataset["url"]))
                    checksum.update(chunk)
                    sink.write(chunk)
        if written != size or checksum.hexdigest() != digest:
            raise BenchError("dataset_hash_mismatch", instance=text(dataset["url"]))
        partial.replace(destination)
        return destination
    except httpx.HTTPError as error:
        raise BenchError("network_unavailable", instance=text(dataset["url"]), exit_code=6) from error
    except OSError as error:
        raise BenchError("target_unreadable", instance=str(destination), exit_code=6) from error
    finally:
        partial.unlink(missing_ok=True)


class VisibleCorpus:
    def __init__(self, root: Path, variant: str, dataset: dict[str, object], settings: BenchSettings) -> None:
        maximum = settings.integer("bench.store.max_line_bytes")
        receipt = read_json(vault_path(root, variant + ".receipt.json"), max_file_bytes=maximum)
        if (
            receipt.get("variant") != variant
            or receipt.get("dataset_sha256") != dataset.get("sha256")
            or receipt.get("examples") != dataset.get("examples")
        ):
            raise BenchError("dataset_hash_mismatch")
        self._cases: dict[str, HealthBenchVisible] = {}
        checksum = sha256()
        try:
            with vault_path(root, variant + ".visible.jsonl").open("rb") as source:
                for _ in range(integer(dataset["examples"])):
                    raw = source.readline(maximum + 1)
                    if not raw or len(raw) > maximum:
                        raise BenchError("dataset_hash_mismatch")
                    checksum.update(raw)
                    row = decode_json(raw)
                    if set(row) != {"prompt_id", "prompt"} or not isinstance(row["prompt"], list):
                        raise BenchError("case_invalid")
                    messages = tuple(self._message(item) for item in row["prompt"])
                    key = text(row["prompt_id"])
                    if not messages or not key or key in self._cases:
                        raise BenchError("case_invalid")
                    self._cases[key] = HealthBenchVisible(key, messages)
                if source.read(1) or checksum.hexdigest() != receipt.get("visible_sha256"):
                    raise BenchError("dataset_hash_mismatch")
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        self.sha256 = checksum.hexdigest()

    @staticmethod
    def _message(value: object) -> tuple[str, str]:
        row = object_map(value)
        if set(row) != {"role", "content"}:
            raise BenchError("case_invalid")
        return text(row["role"]), text(row["content"])

    @property
    def prompt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._cases))

    def visible(self, prompt_id: str) -> HealthBenchVisible:
        if prompt_id not in self._cases:
            raise BenchError("case_invalid")
        return self._cases[prompt_id]
