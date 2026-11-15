from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from crew_health.model.adapters.cli import main
from crew_health.model.adapters.isolation import refuse_holdout
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.domain.release import ModelRefused


@pytest.mark.trace("V33", "R23", "T-E17-060")
def test_tuning_cli_refuses_holdout_and_symlink_before_opening_data(
    store: ManifestStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev, holdout = tmp_path / "dev", tmp_path / "holdout"
    dev.mkdir()
    holdout.mkdir()
    source = holdout / "private.json"
    source.write_text('{"private":"HOLDOUT_CANARY"}', encoding="utf-8")
    link = dev / "looks_dev.json"
    link.symlink_to(source)
    assert (
        main(
            [
                "--root",
                str(store.root),
                "--data-dir",
                str(store.data_dir),
                "--profile",
                "eval",
                "tune-input",
                str(link),
                "--dev",
                str(dev),
                "--holdout",
                str(holdout),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "holdout_path_refused" in output.err
    assert "HOLDOUT_CANARY" not in output.out + output.err
    with pytest.raises(ModelRefused, match="holdout_path_refused"):
        refuse_holdout(source, dev=dev, holdout=holdout)


@pytest.mark.trace("R23", "T-E17-060")
def test_tuning_cli_accepts_only_dev_and_returns_hash_without_content(
    store: ManifestStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev, holdout = tmp_path / "dev", tmp_path / "holdout"
    dev.mkdir()
    holdout.mkdir()
    source = dev / "case.json"
    source.write_text('{"private":"DEV_CANARY"}', encoding="utf-8")
    assert (
        main(
            [
                "--root",
                str(store.root),
                "--data-dir",
                str(store.data_dir),
                "--profile",
                "eval",
                "tune-input",
                str(source),
                "--dev",
                str(dev),
                "--holdout",
                str(holdout),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    doc = json.loads(output.out)
    assert doc["partition"] == "dev" and len(doc["sha256"]) == 64
    assert "DEV_CANARY" not in output.out


@pytest.mark.trace("R22", "T-E17-050")
def test_bundle_stages_exact_hashes_and_keeps_source_unchanged(store: ManifestStore, tmp_path: Path) -> None:
    from crew_health.model.adapters.manifest_store import file_hash
    from crew_health.model.adapters.release_service import ReleaseService

    before = file_hash(store.root / "releases/model_candidate.json")
    destination = tmp_path / "unsigned_bundle"
    result = ReleaseService(store).bundle("model_candidate", destination)
    assert result["signed"] is False and result["activation_allowed"] is False
    members = json.loads((destination / "unsigned-index.json").read_text(encoding="utf-8"))["members"]
    assert members["releases/model_candidate.json"] == before
    assert all(file_hash(destination / name) == digest for name, digest in members.items())
    assert file_hash(store.root / "releases/model_candidate.json") == before
    with pytest.raises(ModelRefused, match="artifact_already_exists"):
        ReleaseService(store).bundle("model_candidate", destination)


@pytest.mark.trace("R23", "T-E17-034")
def test_build_refuses_broken_lineage_even_with_five_measured_reports(
    store: ManifestStore, manifest: dict[str, Any], tmp_path: Path
) -> None:
    from crew_health.model.adapters.manifest_store import durable_json, file_hash
    from crew_health.model.adapters.release_service import ReleaseService

    from .test_integration import live

    live(store, manifest)
    document = cast(dict[str, Any], store.get("model_candidate").document)
    document["release_id"] = "model_unpublished"
    reports = []
    for n in range(1, 6):
        report = {
            "probe": f"P{n}",
            "verdict": "pass",
            "measured": True,
            "weights_sha256": document["weights"]["weights_sha256"],
            "runtime_commit": document["runtime"]["commit"],
        }
        temporary = tmp_path / "report.json"
        durable_json(temporary, report)
        identifier = f"P{n}-" + file_hash(temporary)
        temporary.replace(store.root / "reports" / (identifier + ".json"))
        reports.append(identifier)
    document["evaluation"]["probe_report_ids"] = reports
    candidate = tmp_path / "candidate.json"
    durable_json(candidate, document)
    with pytest.raises(ModelRefused, match="lineage_broken"):
        ReleaseService(store).build(candidate)
    assert not (store.root / "releases/model_unpublished.json").exists()


@pytest.mark.trace("R22", "R23", "T-E17-050", "T-E17-051")
@pytest.mark.parametrize("policy_available", [False, True])
def test_promotion_keeps_append_only_transition_documents_and_is_idempotent(
    store: ManifestStore,
    manifest: dict[str, Any],
    gate: dict[str, object],
    tmp_path: Path,
    policy_available: bool,
) -> None:
    from crew_health.model.adapters.manifest_store import durable_json, file_hash
    from crew_health.model.adapters.release_service import ReleaseService

    from .test_integration import live

    live(store, manifest)
    document = cast(dict[str, Any], store.get("model_candidate").document)

    def report(prefix: str, body: object) -> str:
        temporary = tmp_path / "report.json"
        durable_json(temporary, body)
        digest = file_hash(temporary)
        temporary.replace(store.root / "reports" / (prefix + "-" + digest + ".json"))
        return digest

    lineage = document["weights"]["lineage"]
    lineage["source_files_sha256"] = file_hash(store.root / "pins/huatuogpt-3-9b.json")
    lineage["equivalence_report_sha256"] = report(
        "equivalence",
        {
            "source_files_sha256": lineage["source_files_sha256"],
            "bf16_gguf_sha256": lineage["bf16_gguf_sha256"],
            "runtime_commit": lineage["llama_cpp_commit"],
            "verdict": "pass",
            "measured": True,
            "basis": "Explicit synthetic fixture, never a real measured qualification",
        },
    )
    lineage["reproducibility_report_sha256"] = report(
        "reproduction",
        {
            "verdict": "pass",
            "measured": True,
            "independent_machines": True,
            "first_sha256": document["weights"]["weights_sha256"],
            "second_sha256": document["weights"]["weights_sha256"],
            "basis": "Explicit synthetic fixture",
        },
    )
    ids = []
    for n in range(1, 9):
        prefix = f"P{n}"
        ids.append(
            prefix
            + "-"
            + report(
                prefix,
                {
                    "probe": prefix,
                    "verdict": "pass",
                    "measured": True,
                    "weights_sha256": document["weights"]["weights_sha256"],
                    "runtime_commit": document["runtime"]["commit"],
                    "basis": "Explicit synthetic fixture",
                },
            )
        )
    document["evaluation"]["probe_report_ids"] = ids
    durable_json(store.root / "releases/model_candidate.json", document)
    gate_path = tmp_path / "gate.json"
    durable_json(gate_path, gate)

    class Policy:
        def turn_timeout_seconds(self) -> int:
            return 60

    service = ReleaseService(store, policy=Policy() if policy_available else None)
    if not policy_available:
        with pytest.raises(ModelRefused, match="assistant_policy_unavailable"):
            service.promote("model_candidate", gate_path)
        return
    promoted = service.promote("model_candidate", gate_path)
    assert promoted["status"] == "promoted"
    history = list((store.root / "releases/history/model_candidate").glob("*.json"))
    assert len(history) == 1
    transition = cast(dict[str, Any], store.read_json(history[0]))
    assert transition["previous_document"]["status"] == "candidate"
    assert transition["previous_document"]["evaluation"]["gate_sha256"] is None
    assert transition["document"]["status"] == "promoted"
    before = history[0].read_bytes()
    assert service.promote("model_candidate", gate_path) == promoted
    assert list((store.root / "releases/history/model_candidate").glob("*.json")) == history
    assert history[0].read_bytes() == before
