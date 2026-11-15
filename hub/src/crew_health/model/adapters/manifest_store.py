from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import jsonschema

from crew_health.model.application.ports import (
    ContextSettings,
    ControllerSettings,
    EmbeddingPin,
    JsonValue,
    ModelRelease,
    ModelReleaseRef,
    RetrievalSettings,
    RuntimeSettings,
    ToolCatalog,
)
from crew_health.model.domain.release import ModelRefused, object_value, strict_json, validate_manifest


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def child(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}", name):
        raise ModelRefused("path_refused")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ModelRefused("path_refused")
    return result


def durable_json(path: Path, value: object) -> None:
    """E17's specified node pointer, atomic replace with directory durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(
                json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ManifestStore:
    def __init__(self, root: Path, data_dir: Path, *, profile: str, max_bytes: int = 1048576) -> None:
        self.root, self.data_dir, self.profile, self.max_bytes = root, data_dir, profile, max_bytes

    def read_json(self, path: Path) -> dict[str, object]:
        try:
            with path.open("rb") as stream:
                raw = stream.read(self.max_bytes + 1)
            return object_value(strict_json(raw, max_bytes=self.max_bytes))
        except (OSError, ValueError) as error:
            raise ModelRefused("artifact_unavailable") from error

    def get(self, release_id: str) -> ModelRelease:
        document = self.read_json(child(self.root / "releases", release_id + ".json"))
        return self.parse_document(release_id, document)

    def parse_document(self, release_id: str, document: dict[str, object]) -> ModelRelease:
        schema = self.read_json(self.root / "schemas/model-release.schema.json")
        try:
            jsonschema.Draft202012Validator(schema).validate(document)
        except jsonschema.ValidationError as error:
            raise ModelRefused("manifest_invalid") from error
        validate_manifest(document, profile=self.profile)
        if document["release_id"] != release_id:
            raise ModelRefused("release_identity_mismatch")
        self.verify_folder("prompts", str(document["prompt_version"]))
        self.verify_folder("tool-catalog", str(document["tool_catalog_version"]))
        template = self.root / "templates" / (str(document["chat_template_sha256"]) + ".jinja")
        if not template.is_file() or file_hash(template) != document["chat_template_sha256"]:
            raise ModelRefused("template_hash_mismatch")
        return ModelRelease(json.dumps(document, sort_keys=True, allow_nan=False).encode("utf-8"))

    def verify_folder(self, category: str, version: str) -> None:
        directory = child(self.root / category, version)
        manifest = self.read_json(directory / "MANIFEST.sha256")
        names = {p.name for p in directory.iterdir() if p.is_file() and p.name != "MANIFEST.sha256"}
        if names != manifest.keys():
            raise ModelRefused("artifact_hash_mismatch")
        for name, digest in manifest.items():
            if file_hash(child(directory, name)) != digest:
                raise ModelRefused("artifact_hash_mismatch")

    def fingerprint(self, release_id: str) -> str:
        release = self.get(release_id)
        document = release.document
        digest = hashlib.sha256(release.document_json)
        for category, key in (("prompts", "prompt_version"), ("tool-catalog", "tool_catalog_version")):
            digest.update((child(self.root / category, str(document[key])) / "MANIFEST.sha256").read_bytes())
        return digest.hexdigest()

    def active(self) -> ModelRelease | None:
        path = self.data_dir / "models/active.json"
        if not path.exists():
            return None
        pointer = self.read_json(path)
        return self.get(str(pointer["release_id"]))

    def load(self, release_id: str) -> ModelReleaseRef:
        return ModelReleaseRef(self.get(release_id).document_json)

    def tool_catalog(self, model_release_id: str) -> ToolCatalog:
        release = self.get(model_release_id).document
        doc = self.read_json(
            child(self.root / "tool-catalog", str(release["tool_catalog_version"])) / "tools.json"
        )
        tools = doc.get("tools")
        exposure = object_value(doc.get("exposure"))
        if not isinstance(tools, list):
            raise ModelRefused("catalog_invalid")
        known: set[str] = set()
        output: list[Mapping[str, JsonValue]] = []
        for value in tools:
            tool = object_value(value)
            name = str(tool.get("name"))
            if name in known:
                raise ModelRefused("catalog_invalid")
            known.add(name)
            schema = object_value(tool.get("input_schema"))
            jsonschema.Draft202012Validator.check_schema(schema)
            self._closed(schema)
            output.append(cast(Mapping[str, JsonValue], tool))
        phases: dict[str, tuple[str, ...]] = {}
        for phase, names in exposure.items():
            if not isinstance(names, list) or any(not isinstance(n, str) or n not in known for n in names):
                raise ModelRefused("catalog_invalid")
            phases[phase] = tuple(cast(list[str], names))
        return ToolCatalog(tuple(output), phases)

    def _closed(self, schema: Mapping[str, object]) -> None:
        if schema.get("type") == "object":
            props = object_value(schema.get("properties", {}))
            if schema.get("additionalProperties") is not False or any(
                k in props
                for k in ("scope", "patient_id", "subject_person_id", "actor", "release_id", "mission_id")
            ):
                raise ModelRefused("catalog_scope_refused")
        for value in schema.values():
            if isinstance(value, dict):
                if "type" in value:
                    self._closed(value)
                else:
                    for sub in value.values():
                        if isinstance(sub, dict):
                            self._closed(sub)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._closed(item)

    def _settings(self, release_id: str, name: str) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], self.get(release_id).document[name])

    def retrieval_settings(self, model_release_id: str) -> RetrievalSettings:
        return RetrievalSettings(self._settings(model_release_id, "retrieval_settings"))

    def context_settings(self, model_release_id: str) -> ContextSettings:
        return ContextSettings(self._settings(model_release_id, "context_settings"))

    def runtime_settings(self, model_release_id: str) -> RuntimeSettings:
        return RuntimeSettings(self._settings(model_release_id, "runtime"))

    def controller_settings(self, model_release_id: str) -> ControllerSettings:
        return ControllerSettings(self._settings(model_release_id, "controller"))

    def embedding_pin(self) -> EmbeddingPin:
        release = self.active()
        if release is None:
            raise ModelRefused("release_not_found")
        pin = object_value(release.document["embedding"])
        return EmbeddingPin(
            str(pin["model_id"]), str(pin["revision"]), str(pin["sha256"]), int(cast(int, pin["dims"]))
        )
