from __future__ import annotations

import re
from collections.abc import Mapping

from crew_health.model.adapters.manifest_store import ManifestStore, file_hash
from crew_health.model.domain.release import ModelRefused, object_value, sha


class FileReleaseEvidence:
    def __init__(self, store: ManifestStore) -> None:
        self.store = store

    def reports(self, release_id: str) -> tuple[Mapping[str, object], ...]:
        document = self.store.get(release_id).document
        evaluation = object_value(document["evaluation"])
        ids = evaluation["probe_report_ids"]
        if not isinstance(ids, list) or len(ids) > 64:
            raise ModelRefused("probe_inventory_invalid")
        output = []
        for identifier in ids:
            if not isinstance(identifier, str) or not re.fullmatch(r"P[1-8]-[a-f0-9]{64}", identifier):
                raise ModelRefused("probe_inventory_invalid")
            path = self.store.root / "reports" / (identifier + ".json")
            if file_hash(path) != identifier.split("-", 1)[1]:
                raise ModelRefused("probe_hash_mismatch")
            output.append(self.store.read_json(path))
        return tuple(output)

    def _report_for_hash(self, digest: str) -> dict[str, object]:
        sha(digest)
        candidates = list((self.store.root / "reports").glob("*-" + digest + ".json"))
        if len(candidates) != 1 or file_hash(candidates[0]) != digest:
            raise ModelRefused("lineage_broken")
        return self.store.read_json(candidates[0])

    def lineage(self, release_id: str) -> bool:
        return self.lineage_document(self.store.get(release_id).document)

    def lineage_document(self, document: Mapping[str, object]) -> bool:
        try:
            weights = object_value(document["weights"])
            lineage = object_value(weights["lineage"])
            equivalence = self._report_for_hash(str(lineage["equivalence_report_sha256"]))
            reproduction = self._report_for_hash(str(lineage["reproducibility_report_sha256"]))
            source_pin = self.store.root / "pins/huatuogpt-3-9b.json"
            runtime_pin = self.store.read_json(self.store.root / "pins/llama-cpp.json")
            return (
                file_hash(source_pin) == lineage["source_files_sha256"]
                and equivalence.get("source_files_sha256") == lineage["source_files_sha256"]
                and equivalence.get("bf16_gguf_sha256") == lineage["bf16_gguf_sha256"]
                and equivalence.get("runtime_commit") == lineage["llama_cpp_commit"] == runtime_pin["commit"]
                and equivalence.get("verdict") == "pass"
                and equivalence.get("measured") is True
                and reproduction.get("verdict") == "pass"
                and reproduction.get("measured") is True
                and reproduction.get("independent_machines") is True
                and reproduction.get("first_sha256")
                == reproduction.get("second_sha256")
                == weights["weights_sha256"]
            )
        except OSError, ModelRefused, KeyError:
            return False
