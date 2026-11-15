from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import cast

from crew_health.model.adapters.manifest_store import ManifestStore, durable_json, file_hash
from crew_health.model.application.ports import JsonValue, ModelActivationPublisher, ModelArtifactTrust
from crew_health.model.domain.release import ModelRefused, object_value


class ActivationService:
    def __init__(
        self,
        store: ManifestStore,
        *,
        now: Callable[[], datetime],
        trust: ModelArtifactTrust | None = None,
        publisher: ModelActivationPublisher | None = None,
    ) -> None:
        self.store, self.now = store, now
        self.trust, self.publisher = trust, publisher

    def activate(self, release_id: str, *, actor_id: str) -> dict[str, object]:
        release = self.store.get(release_id)
        document = release.document
        if self.store.profile == "flight" and document["status"] != "promoted":
            raise ModelRefused("not_promoted")
        if self.store.profile == "flight":
            if self.trust is None or self.publisher is None:
                raise ModelRefused("signed_audited_activation_binding_unavailable")
            self.trust.verify(release)
        if document["mode"] == "live":
            weights = object_value(document["weights"])
            path = self.store.data_dir / "models" / str(weights["file_name"])
            if not path.is_file() or path.is_symlink() or file_hash(path) != weights["weights_sha256"]:
                raise ModelRefused("weights_hash_mismatch")
        pointer = self.store.data_dir / "models/active.json"
        previous = self.store.read_json(pointer) if pointer.exists() else None
        if previous is not None and previous["release_id"] == release_id:
            return previous
        result = {
            "release_id": release_id,
            "previous_release_id": previous["release_id"] if previous else None,
            "activated_at": self.now().isoformat(),
            "activated_by": actor_id,
        }
        if self.store.profile == "flight":
            assert self.publisher is not None
            self.publisher.publish(
                cast(dict[str, JsonValue], result),
                expected_release_id=str(previous["release_id"]) if previous else None,
                actor_id=actor_id,
                model_release_sha256=hashlib.sha256(release.document_json).hexdigest(),
            )
        else:
            durable_json(pointer, result)
        return result

    def rollback(self, *, actor_id: str) -> dict[str, object]:
        path = self.store.data_dir / "models/active.json"
        if not path.exists():
            raise ModelRefused("no_previous_release")
        pointer = self.store.read_json(path)
        previous = pointer.get("previous_release_id")
        if not isinstance(previous, str):
            raise ModelRefused("no_previous_release")
        return self.activate(previous, actor_id=actor_id)
