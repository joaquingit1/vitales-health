from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import jsonschema

from crew_health.model.adapters.manifest_store import ManifestStore, child, durable_json, file_hash
from crew_health.model.adapters.release_evidence import FileReleaseEvidence
from crew_health.model.application.ports import ModelPromotionContext
from crew_health.model.application.promotion import promote
from crew_health.model.domain.release import ModelRefused, object_value, validate_manifest


class ReleaseService:
    def __init__(self, store: ManifestStore, *, policy: ModelPromotionContext | None = None) -> None:
        self.store, self.policy = store, policy

    def build(self, manifest_path: Path) -> dict[str, object]:
        document = self.store.read_json(manifest_path)
        validate_manifest(document, profile=self.store.profile)
        jsonschema.Draft202012Validator(
            self.store.read_json(self.store.root / "schemas/model-release.schema.json")
        ).validate(document)
        if document["status"] != "candidate":
            raise ModelRefused("not_candidate")
        destination = child(self.store.root / "releases", str(document["release_id"]) + ".json")
        if destination.exists():
            existing = self.store.read_json(destination)
            if existing == document:
                return existing
            raise ModelRefused("release_content_conflict")
        if document["mode"] == "live":
            if not FileReleaseEvidence(self.store).lineage_document(document):
                raise ModelRefused("lineage_broken")
            # Evidence is checked without publishing even a candidate on an incomplete qualification.
            weights = object_value(document["weights"])
            runtime = object_value(document["runtime"])
            ids = object_value(document["evaluation"])["probe_report_ids"]
            if not isinstance(ids, list):
                raise ModelRefused("probes_missing")
            reports = []
            for identifier in ids:
                path = child(self.store.root / "reports", str(identifier) + ".json")
                digest = str(identifier).split("-", 1)[-1]
                if file_hash(path) != digest:
                    raise ModelRefused("probe_hash_mismatch")
                reports.append(self.store.read_json(path))
            qualified = {
                r.get("probe")
                for r in reports
                if r.get("measured") is True
                and r.get("verdict") == "pass"
                and r.get("weights_sha256") == weights["weights_sha256"]
                and r.get("runtime_commit") == runtime["commit"]
            }
            if not {f"P{n}" for n in range(1, 6)} <= qualified:
                raise ModelRefused("probes_missing")
        self.store.parse_document(str(document["release_id"]), document)
        self._publish(document, previous=None, reason="candidate_built")
        return document

    def bundle(self, release_id: str, destination: Path) -> dict[str, object]:
        if self.store.profile == "flight":
            raise ModelRefused("build_operations_not_available_in_flight")
        if destination.exists():
            raise ModelRefused("artifact_already_exists")
        release = self.store.get(release_id)
        document = release.document
        paths = [child(self.store.root / "releases", release_id + ".json")]
        for category, version_key in (
            ("prompts", "prompt_version"),
            ("tool-catalog", "tool_catalog_version"),
        ):
            paths.extend(child(self.store.root / category, str(document[version_key])).iterdir())
        paths.extend((self.store.root / "templates" / (str(document["chat_template_sha256"]) + ".jinja"),))
        for category in ("schemas", "pins", "policy"):
            paths.extend((self.store.root / category).glob("*.json"))
        evaluation = object_value(document["evaluation"])
        for key in ("probe_report_ids", "dev_report_ids"):
            ids = evaluation[key]
            if not isinstance(ids, list):
                raise ModelRefused("probes_missing")
            paths.extend(child(self.store.root / "reports", str(identifier) + ".json") for identifier in ids)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".model-bundle-"))
        try:
            members: dict[str, str] = {}
            for source in paths:
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or not source.resolve().is_relative_to(self.store.root.resolve())
                ):
                    raise ModelRefused("artifact_unavailable")
                name = source.relative_to(self.store.root).as_posix()
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                members[name] = file_hash(target)
            if document["mode"] == "live":
                if not FileReleaseEvidence(self.store).lineage(release_id):
                    raise ModelRefused("lineage_broken")
                weights = object_value(document["weights"])
                source = child(self.store.data_dir / "models", str(weights["file_name"]))
                if source.is_symlink() or file_hash(source) != weights["weights_sha256"]:
                    raise ModelRefused("weights_hash_mismatch")
                name = "models/" + str(weights["file_name"])
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o444)
                members[name] = file_hash(target)
                if members[name] != weights["weights_sha256"]:
                    raise ModelRefused("weights_hash_mismatch")
            staged = ManifestStore(
                temporary, temporary, profile=self.store.profile, max_bytes=self.store.max_bytes
            )
            if staged.get(release_id).document_json != release.document_json:
                raise ModelRefused("release_changed")
            index = {
                "release_id": release_id,
                "members": members,
                "signed": False,
                "activation_allowed": False,
                "next_step": "E20 signs and verifies the completed package; this index is not a signature.",
            }
            durable_json(temporary / "unsigned-index.json", index)
            os.rename(temporary, destination)
            return index
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _publish(
        self, document: dict[str, object], *, previous: dict[str, object] | None, reason: str
    ) -> None:
        release_id = str(document["release_id"])
        history = child(self.store.root / "releases/history", release_id) / (uuid.uuid4().hex + ".json")
        durable_json(
            history,
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "reason": reason,
                "previous_document": previous,
                "document": document,
            },
        )
        durable_json(child(self.store.root / "releases", release_id + ".json"), document)

    def promote(self, release_id: str, gate_path: Path) -> dict[str, object]:
        document = self.store.get(release_id).document
        gate_digest = file_hash(gate_path)
        if document["status"] == "promoted":
            if object_value(document["evaluation"])["gate_sha256"] == gate_digest:
                return cast(dict[str, object], document)
            raise ModelRefused("release_content_conflict")
        if self.policy is None:
            raise ModelRefused("assistant_policy_unavailable")
        timeout = self.policy.turn_timeout_seconds()
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout <= 0
            or object_value(document["evaluated_with"])["turn_timeout_seconds"] != timeout
        ):
            raise ModelRefused("assistant_policy_mismatch")
        gate = self.store.read_json(gate_path)
        policy = self.store.read_json(self.store.root / "policy/promotion-policy.json")
        result = promote(cast(dict[str, object], document), gate, policy, FileReleaseEvidence(self.store))
        # Keep the evaluated gate hash in the exact immutable candidate provenance.
        evaluation = dict(object_value(result["evaluation"]))
        evaluation["gate_sha256"] = gate_digest
        result["evaluation"] = evaluation
        self._publish(result, previous=cast(dict[str, object], document), reason="promoted")
        return result
