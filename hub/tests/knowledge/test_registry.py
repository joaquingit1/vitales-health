import json
import re
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemoryIds, MemorySettings
from crew_health.knowledge.adapters.registry_file import (
    build_registry_input,
    input_copies,
    load_manifest,
    pinned_file,
    registry_snapshot,
)
from crew_health.knowledge.application.registry_service import RegistryService
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.registry import (
    Active,
    Condition,
    DeclaredSource,
    RegistrySnapshot,
    RegistryUnavailable,
    RemovedOrSplit,
    SourceUnknown,
    UnknownCondition,
)


def condition() -> Condition:
    rows = json.loads(Path("docs/product/condition-assessments-119.json").read_text())
    row = rows[0]
    return Condition(
        row["condition_id"],
        row["condition"],
        "active",
        row["nasa_membership_source"],
        row["family"],
        tuple(sorted(row.items())),
        (row["workup_source_ids"],),
        True,
    )


def service() -> RegistryService:
    line = next(
        row
        for row in Path("knowledge/inputs/package/NASA-SOURCES.md").read_text().splitlines()
        if row.startswith("| EV01, RT03 |")
    )
    title, url = re.findall(r"\[([^]]+)\]\(([^)]+)\)", line.split("|")[2])[0]
    sources = tuple(
        DeclaredSource(identifier, title, url, "NASA-SOURCES.md", None, None, None)
        for identifier in ("EV01", "RT03")
    )
    snapshot = RegistrySnapshot("a" * 64, (condition(),), (), sources, ())
    return RegistryService(
        snapshot,
        ids=MemoryIds(identifiers=()),
        max_rows=1000,
        max_source_ids=200,
        max_url_chars=1000,
        tracking_parameters=(),
    )


@pytest.mark.trace("T-E10-04", "T-E10-28", "T-E10-30", "R13", "D28", "D32")
def test_condition_identity_page_and_missing_protocol_are_preserved() -> None:
    reader = service()
    item = reader.get("ICL1")
    assert item == condition()
    assert isinstance(reader.condition_status("ICL1"), Active)
    assert isinstance(reader.condition_status("ICL0"), UnknownCondition)
    assert reader.active_ids() == ("ICL1",)
    assert item.workup_review_required
    assert isinstance(reader.resolve("ICL1"), RegistryUnavailable)


@pytest.mark.trace("T-E10-04", "R13")
def test_removed_condition_never_becomes_active() -> None:
    snapshot = replace(service().snapshot, conditions=(replace(condition(), status="removed_or_split"),))
    reader = RegistryService(
        snapshot,
        ids=MemoryIds(identifiers=()),
        max_rows=1000,
        max_source_ids=200,
        max_url_chars=1000,
        tracking_parameters=(),
    )
    assert reader.active_ids() == ()
    assert isinstance(reader.condition_status("ICL1"), RemovedOrSplit)


@pytest.mark.trace("T-E10-06", "T-E10-29", "T-E10-51", "I2", "C11")
def test_aliases_remain_distinct_declarations_of_same_document() -> None:
    reader = service()
    first, second = reader.summary("EV01"), reader.summary("RT03")
    assert not isinstance(first, SourceUnknown)
    assert not isinstance(second, SourceUnknown)
    assert first.catalog_document_key == second.catalog_document_key
    assert first.source_id != second.source_id
    report = reader.resolve_many(("EV01", "RT03", "S99"))
    assert report.unknown == ("S99",)
    assert len(report.resolved) == 2
    extra = DeclaredSource("EV01", first.title, "https://example.com/conflict", "candidate", None, None, None)
    report = reader.resolve_many(("EV01",), (extra,))
    assert report.conflicts == ("EV01",)
    assert report.resolved == ()


@pytest.mark.trace("T-E10-06", "T-E10-29", "I2")
def test_conflicting_source_declarations_reject_whole_snapshot() -> None:
    original = service()
    bad = replace(original.snapshot.sources[0], url="https://example.com/other", declaring_input="candidate")
    with pytest.raises(EvidenceInvalid):
        RegistryService(
            replace(original.snapshot, sources=(*original.snapshot.sources, bad)),
            ids=MemoryIds(identifiers=()),
            max_rows=1000,
            max_source_ids=200,
            max_url_chars=1000,
            tracking_parameters=(),
        )


@pytest.mark.trace("I8")
def test_normal_lookup_reuses_validated_snapshot() -> None:
    reader = service()
    report = reader.resolve_many(("EV01",))
    assert report.resolved[0] is reader.summary("EV01")


@pytest.mark.trace("T-E10-31", "D7", "C11")
def test_sourced_label_change_requires_no_code_change() -> None:
    first = service()
    source = first.snapshot.sources[0]
    snapshot = replace(first.snapshot, sources=(replace(source, title=first.snapshot.conditions[0].title),))
    second = RegistryService(
        snapshot,
        ids=MemoryIds(identifiers=()),
        max_rows=1000,
        max_source_ids=200,
        max_url_chars=1000,
        tracking_parameters=(),
    )
    assert second.summary(source.source_id).title == first.snapshot.conditions[0].title
    assert first.summary(source.source_id).title == source.title


@pytest.mark.trace("T-E10-01", "T-E10-02", "T-E10-05", "T-E10-28", "T-E10-45", "E10-01", "R13", "I1")
def test_real_local_inventory_counts_and_separate_mappings(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    copies = input_copies(Path.cwd(), manifest, settings)
    definitions = Path("knowledge/inputs/inventories.json").read_bytes()
    value = build_registry_input(copies, sha256(manifest).hexdigest(), definitions, settings)
    snapshot = registry_snapshot(value, json.loads(definitions)["planning_sentinel"])
    checklist = json.loads(Path("fixtures/knowledge/source-checklist.json").read_bytes())
    assert len(snapshot.conditions) == checklist["active_conditions"]
    assert len(snapshot.memberships) == checklist["memberships_total"]
    assert {c.condition_id for c in snapshot.conditions} == {c.condition_id for c in value.conditions}
    for identifier, count in checklist["inventory_counts"].items():
        assert sum(m.inventory_id == identifier for m in snapshot.memberships) == count
    member = next(m for m in snapshot.memberships if m.source_entry_id == "RCL2")
    assert member.published_mapping == ("ICL82",)
    assert member.title_match_mapping == ()
    assert all("PLANNING_REVIEW_REQUIRED" not in c.workup_source_ids for c in snapshot.conditions)
    assert all(c.workup_review_required for c in snapshot.conditions)


@pytest.mark.trace("T-E10-32", "E10-01", "I1")
def test_incorrect_inventory_count_rejected(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    copies = input_copies(Path.cwd(), manifest, settings)
    definitions = json.loads(Path("knowledge/inputs/inventories.json").read_bytes())
    definitions["inventories"][0]["expected_count"] += 1
    with pytest.raises(EvidenceInvalid, match="inventory_count"):
        build_registry_input(copies, sha256(manifest).hexdigest(), json.dumps(definitions).encode(), settings)


@pytest.mark.trace("T-E10-01", "T-E10-28", "R13", "I1")
def test_split_icl_identifiers_are_never_truncated(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    value = build_registry_input(
        input_copies(Path.cwd(), manifest, settings),
        sha256(manifest).hexdigest(),
        Path("knowledge/inputs/inventories.json").read_bytes(),
        settings,
    )
    snapshot = registry_snapshot(value, "PLANNING_REVIEW_REQUIRED")
    assert {c.condition_id for c in snapshot.conditions if c.condition_id.startswith("ICL36")} == {
        "ICL36A",
        "ICL36B",
    }
    assert all("ICL36" not in m.title_match_mapping for m in snapshot.memberships)


@pytest.mark.trace("T-E10-06", "T-E10-29", "I2", "C11")
def test_d23_multilink_n02_retains_every_document(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    value = build_registry_input(
        input_copies(Path.cwd(), manifest, settings),
        sha256(manifest).hexdigest(),
        Path("knowledge/inputs/inventories.json").read_bytes(),
        settings,
    )
    sources = [s for s in value.sources if s.source_id == "N02"]
    assert len({s.url for s in sources}) == 2
    reader = RegistryService(
        replace(
            registry_snapshot(value, "PLANNING_REVIEW_REQUIRED"),
            sources=tuple(
                s
                for s in registry_snapshot(value, "PLANNING_REVIEW_REQUIRED").sources
                if s.source_id == "N02"
            ),
        ),
        ids=MemoryIds(identifiers=()),
        max_rows=100000,
        max_source_ids=200,
        max_url_chars=1000,
        tracking_parameters=(),
    )
    summary = reader.summary("N02")
    assert not isinstance(summary, SourceUnknown)
    assert {document.url for document in summary.documents} == {s.url for s in sources}
    assert len(set(summary.catalog_document_keys)) == 2
    report = reader.resolve_many(("N02",))
    assert report.resolved == (summary,)
    assert report.conflicts == ()
    extra = replace(summary.documents[0], declaring_input="candidate")
    candidate = reader.resolve_many(("N02",), (extra,)).resolved[0]
    assert {document.url for document in candidate.documents} == {s.url for s in sources}
    assert "candidate" in candidate.declaring_inputs
    bad = replace(extra, url="https://example.com/conflict")
    assert reader.resolve_many(("N02",), (bad,)).conflicts == ("N02",)


@pytest.mark.trace("T-E10-06", "T-E10-29", "I2", "C11")
def test_disagreeing_document_declarations_still_require_curation(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    value = build_registry_input(
        input_copies(Path.cwd(), manifest, settings),
        sha256(manifest).hexdigest(),
        Path("knowledge/inputs/inventories.json").read_bytes(),
        settings,
    )
    sources = tuple(
        s for s in registry_snapshot(value, "PLANNING_REVIEW_REQUIRED").sources if s.source_id == "RT02"
    )
    assert len({s.url for s in sources}) == 2
    assert len({s.declaring_input for s in sources}) == 2
    with pytest.raises(EvidenceInvalid, match="source_conflict"):
        RegistryService(
            RegistrySnapshot(sha256(manifest).hexdigest(), (), (), sources, ()),
            ids=MemoryIds(identifiers=()),
            max_rows=100000,
            max_source_ids=200,
            max_url_chars=1000,
            tracking_parameters=(),
        )


@pytest.mark.trace("T-E10-29", "T-E10-39", "C11", "I1")
def test_capture_date_is_not_a_publication_date(settings: MemorySettings) -> None:
    manifest = Path("knowledge/inputs/manifest.json").read_bytes()
    value = build_registry_input(
        input_copies(Path.cwd(), manifest, settings),
        sha256(manifest).hexdigest(),
        Path("knowledge/inputs/inventories.json").read_bytes(),
        settings,
    )
    assert all(s.date_text is None for s in value.sources if s.declaring_input == "sources.csv")


@pytest.mark.trace("T-E10-39", "T-E10-19", "I7", "V33")
def test_manifest_rejects_unknown_and_mismatched_paths(settings: MemorySettings) -> None:
    manifest = json.loads(Path("knowledge/inputs/manifest.json").read_bytes())
    manifest["files"][0]["source"] = "eval/holdout/canary.json"
    with pytest.raises(EvidenceInvalid, match="input_manifest"):
        load_manifest(json.dumps(manifest).encode(), settings)
    manifest = json.loads(Path("knowledge/inputs/manifest.json").read_bytes())
    manifest["files"][0]["destination"] = manifest["files"][1]["destination"]
    with pytest.raises(EvidenceInvalid):
        load_manifest(json.dumps(manifest).encode(), settings)


@pytest.mark.trace("T-E10-39", "T-E10-19", "I7", "V33")
def test_pinned_file_rejects_missing_tampered_and_symlink(tmp_path: Path) -> None:
    public = Path("docs/product/NASA-SOURCES.md").read_bytes()
    expected = sha256(public).hexdigest()
    with pytest.raises(EvidenceInvalid, match="missing_input"):
        pinned_file(tmp_path, "source.md", expected, len(public))
    (tmp_path / "source.md").write_bytes(public)
    assert pinned_file(tmp_path, "source.md", expected, len(public)) == public
    with pytest.raises(EvidenceInvalid, match="input_sha256"):
        pinned_file(tmp_path, "source.md", "0" * 64, len(public))
    (tmp_path / "alias.md").symlink_to(tmp_path / "source.md")
    with pytest.raises(EvidenceInvalid, match="input_path"):
        pinned_file(tmp_path, "alias.md", expected, len(public))
    with pytest.raises(EvidenceInvalid, match="input_path"):
        pinned_file(tmp_path, "../source.md", expected, len(public))
