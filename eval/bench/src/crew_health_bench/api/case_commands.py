from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from crew_health.kernel.ids import IdFactory

from crew_health_bench.adapters.case_documents import CaseDocuments, LoadedCase
from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.files import bounded_files
from crew_health_bench.adapters.isolation import refuse_repository_plaintext
from crew_health_bench.adapters.owner import AgeOwnerGrant
from crew_health_bench.adapters.partitions import verify_partitions
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.application.partitioning import FreezePartitions
from crew_health_bench.domain.cases import Partition, validate_cases
from crew_health_bench.domain.documents import object_map, text, text_list
from crew_health_bench.domain.errors import BenchError


def load_cases(paths: Sequence[Path], *, documents: CaseDocuments, max_files: int) -> tuple[LoadedCase, ...]:
    files: list[Path] = []
    for path in paths:
        discovered = bounded_files(path, max_files=max_files)
        files.extend(file for file in discovered if path.is_file() or file.name.endswith(".case.json"))
        if len(files) > max_files:
            raise BenchError("case_invalid")
    if not files or len(set(files)) != len(files):
        raise BenchError("case_invalid")
    loaded = tuple(documents.load(path) for path in sorted(files))
    validate_cases(tuple(row.case for row in loaded))
    return loaded


def validate(
    paths: Sequence[Path],
    *,
    config_dir: Path,
    settings: BenchSettings,
    ids: IdFactory,
    hidden_dir: Path | None,
) -> tuple[LoadedCase, ...]:
    maximum = settings.integer("bench.leak.max_file_bytes")
    documents = CaseDocuments.from_config(config_dir, max_file_bytes=maximum, ids=ids)
    cases = load_cases(paths, documents=documents, max_files=settings.integer("bench.scan.max_files"))
    if hidden_dir is not None:
        for case in cases:
            documents.hidden_for(case, hidden_dir / (case.case.case_id + ".hidden.json"))
    return cases


def _previous(root: Path, maximum: int) -> dict[str, Partition]:
    if not (root / "partitions/FROZEN.sha256").exists():
        return {}
    verify_partitions(root, max_file_bytes=maximum)
    document = read_json(root / "partitions/manifest.json", max_file_bytes=maximum)
    rows = document.get("partitions")
    if not isinstance(rows, list):
        raise BenchError("case_invalid")
    result: dict[str, Partition] = {}
    for value in rows:
        row = object_map(value)
        case_id = text(row.get("case_id"))
        if case_id in result:
            raise BenchError("case_invalid")
        try:
            result[case_id] = Partition(text(row.get("partition")))
        except ValueError as error:
            raise BenchError("case_invalid") from error
    return result


def freeze(
    paths: Sequence[Path],
    *,
    config_dir: Path,
    settings: BenchSettings,
    ids: IdFactory,
    hidden_dir: Path,
    salt_path: Path,
    fp_salt: str,
) -> None:
    maximum = settings.integer("bench.leak.max_file_bytes")
    policy = load_document(config_dir / "partition-policy.json", max_file_bytes=maximum)
    owner = AgeOwnerGrant(
        recipients=text_list(object_map(policy["vault"])["recipients"]),
        environment=os.environ,
        max_file_bytes=maximum,
    )
    owner.require_owner()
    repository = config_dir.resolve().parents[2]
    refuse_repository_plaintext((*paths, hidden_dir), repository=repository)
    salt = owner.salt(salt_path, repository=repository)
    documents = CaseDocuments.from_config(config_dir, max_file_bytes=maximum, ids=ids)
    cases = load_cases(paths, documents=documents, max_files=settings.integer("bench.scan.max_files"))
    texts = {
        case.case.case_id: documents.hidden_for(case, hidden_dir / (case.case.case_id + ".hidden.json"))[1]
        for case in cases
    }
    root = config_dir.parent
    previous = _previous(root, maximum)
    with FileArtifactStore(root, max_line_bytes=maximum, max_records=len(cases)) as store:
        FreezePartitions(store=store, owner=owner).freeze(
            tuple(row.case for row in cases),
            policy,
            salt=salt,
            fp_salt=fp_salt,
            texts=texts,
            previous=previous,
        )
