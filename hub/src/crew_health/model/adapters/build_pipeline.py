"""Development-only artifact operations. Nothing here is called on a flight node."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path, PurePosixPath
from typing import cast

from crew_health.model.adapters.manifest_store import ManifestStore, child, durable_json, file_hash
from crew_health.model.domain.release import ModelRefused, object_value, positive, sha, strict_json


class BuildPipeline:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = ManifestStore(root, root, profile="eval")
        self.settings = self.store.read_json(root / "build/settings.json")
        positive(self.settings.get("max_files"), maximum=1024)
        positive(self.settings.get("max_source_bytes"), maximum=100000000000)
        positive(self.settings.get("process_timeout_seconds"), maximum=14400)

    def pin(self, revision: str, *, name: str) -> dict[str, object]:
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ModelRefused("revision_mismatch")
        repo = "FreedomIntelligence/HuatuoGPT-3-9B"
        destination = child(self.root / "pins", name + ".json")
        if destination.exists():
            prior = self.store.read_json(destination)
            if prior.get("revision") != revision:
                raise ModelRefused("revision_mismatch")
            return prior
        url = f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"
        with urllib.request.urlopen(
            url, timeout=cast(int, self.settings["network_timeout_seconds"])
        ) as response:
            metadata = object_value(
                strict_json(response.read(self.store.max_bytes + 1), max_bytes=self.store.max_bytes)
            )
        if metadata.get("sha") != revision:
            raise ModelRefused("revision_mismatch")
        entries = metadata.get("siblings")
        if not isinstance(entries, list) or len(entries) > cast(int, self.settings["max_files"]):
            raise ModelRefused("source_bound")
        files: list[dict[str, object]] = []
        for value in entries:
            entry = object_value(value)
            filename = str(entry["rfilename"])
            if PurePosixPath(filename).is_absolute() or ".." in PurePosixPath(filename).parts:
                raise ModelRefused("source_path_refused")
            lfs = entry.get("lfs")
            digest = object_value(lfs).get("sha256") if lfs else None
            if digest is None:
                with urllib.request.urlopen(
                    f"https://huggingface.co/{repo}/resolve/{revision}/{filename}",
                    timeout=cast(int, self.settings["network_timeout_seconds"]),
                ) as response:
                    content = response.read(self.store.max_bytes + 1)
                if len(content) > self.store.max_bytes:
                    raise ModelRefused("source_bound")
                digest = hashlib.sha256(content).hexdigest()
            files.append({"name": filename, "sha256": sha(digest), "size_bytes": positive(entry["size"])})
        card = object_value(metadata.get("cardData"))
        license_name = card.get("license")
        if not isinstance(license_name, str):
            raise ModelRefused("license_provenance_unavailable")
        readme = next((f for f in files if f["name"] == "README.md"), None)
        if readme is None:
            raise ModelRefused("license_provenance_unavailable")
        result = {
            "repo": repo,
            "revision": revision,
            "files": files,
            "license": license_name,
            "license_sha256": readme["sha256"],
            "license_basis": "Publisher README front matter, no standalone LICENSE assumed",
            "source_url": f"https://huggingface.co/{repo}/tree/{revision}",
        }
        durable_json(destination, result)
        return result

    def verify_source_tree(self, source: Path) -> None:
        pin = self.store.read_json(self.root / "pins/llama-source-files.json")
        for name, digest in object_value(pin["files"]).items():
            path = source / name
            if not path.resolve().is_relative_to(source.resolve()) or file_hash(path) != digest:
                raise ModelRefused("converter_source_mismatch")

    def fetch(self, pin: Path, directory: Path) -> dict[str, object]:
        document = self.store.read_json(pin)
        revision = str(document["revision"])
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ModelRefused("revision_mismatch")
        repo = str(document["repo"])
        if repo != "FreedomIntelligence/HuatuoGPT-3-9B":
            raise ModelRefused("repository_not_pinned")
        files = document["files"]
        if not isinstance(files, list) or len(files) > cast(int, self.settings["max_files"]):
            raise ModelRefused("source_bound")
        directory.mkdir(parents=True, exist_ok=True)
        for value in files:
            entry = object_value(value)
            name = str(entry["name"])
            size = positive(entry["size_bytes"])
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ModelRefused("source_path_refused")
            target = directory / name
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ModelRefused("source_path_refused")
            if target.is_file() and target.stat().st_size == size and file_hash(target) == entry["sha256"]:
                continue
            if size > cast(int, self.settings["max_source_bytes"]) or shutil.disk_usage(
                directory
            ).free - size < cast(int, self.settings["minimum_free_bytes"]):
                raise ModelRefused("disk_admission_refused")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".partial")
            digest = hashlib.sha256()
            received = 0
            try:
                with (
                    urllib.request.urlopen(
                        f"https://huggingface.co/{repo}/resolve/{revision}/{name}",
                        timeout=cast(int, self.settings["network_timeout_seconds"]),
                    ) as response,
                    temporary.open("wb") as output,
                ):
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        received += len(block)
                        if received > size:
                            raise ModelRefused("source_size_mismatch")
                        output.write(block)
                        digest.update(block)
                    output.flush()
                    os.fsync(output.fileno())
                if received != size or digest.hexdigest() != entry["sha256"]:
                    raise ModelRefused("source_hash_mismatch")
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return {"revision": revision, "verified_files": len(files), "source_files_sha256": file_hash(pin)}

    def convert(self, source: Path, checkpoint: Path, output: Path, *, python: Path) -> dict[str, object]:
        self.verify_source_tree(source)
        self.fetch(self.root / "pins/huatuogpt-3-9b.json", checkpoint)
        if output.exists():
            raise ModelRefused("artifact_already_exists")
        if shutil.disk_usage(output.parent).free < cast(int, self.settings["minimum_conversion_free_bytes"]):
            raise ModelRefused("disk_admission_refused")
        self._run(
            (
                str(python),
                str(source / "convert_hf_to_gguf.py"),
                str(checkpoint),
                "--outfile",
                str(output),
                "--outtype",
                "bf16",
                "--no-mtp",
            ),
            cwd=source,
        )
        return {
            "bf16_gguf_sha256": file_hash(output),
            "converter_commit": self.store.read_json(self.root / "pins/llama-cpp.json")["commit"],
            "source_files_sha256": file_hash(self.root / "pins/huatuogpt-3-9b.json"),
            "measured": True,
            "claim": "Format conversion only; tensor transform equivalence and P1/P2 still require separate verification.",
        }

    def verify_equivalence(
        self, checkpoint: Path, artifact: Path, output: Path, *, python: Path
    ) -> dict[str, object]:
        if output.exists():
            raise ModelRefused("artifact_already_exists")
        self.fetch(self.root / "pins/huatuogpt-3-9b.json", checkpoint)
        self._run(
            (
                str(python),
                str((self.root / "build/verify_tensors.py").resolve()),
                str(checkpoint.resolve()),
                str(artifact.resolve()),
                str(output.resolve()),
            ),
            cwd=output.parent,
        )
        result = self.store.read_json(output)
        if result.get("verdict") != "pass" or result.get("measured") is not True:
            raise ModelRefused("equivalence_failed")
        return {"equivalence_report_sha256": file_hash(output), "verdict": "pass", "measured": True}

    def quantize(self, source: Path, output: Path, *, binary: Path, kind: str) -> dict[str, object]:
        if kind not in ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"):
            raise ModelRefused("quantization_refused")
        if output.exists() or output.resolve() == source.resolve():
            raise ModelRefused("artifact_already_exists")
        threads = positive(self.settings.get("quantization_threads"), maximum=6)
        self._run((str(binary), str(source), str(output), kind, str(threads)), cwd=output.parent)
        return {
            "input_sha256": file_hash(source),
            "output_sha256": file_hash(output),
            "quantization": kind,
            "measured": True,
        }

    def reproduce(
        self, first: Path, second: Path, *, first_machine: str, second_machine: str
    ) -> dict[str, object]:
        if first.resolve() == second.resolve():
            raise ModelRefused("independent_build_required")
        same = file_hash(first) == file_hash(second)
        independent = first_machine != second_machine
        return {
            "verdict": "pass" if same and independent else "partial" if same else "fail",
            "measured": True,
            "first_sha256": file_hash(first),
            "second_sha256": file_hash(second),
            "independent_machines": independent,
            "basis": "E17 P2 requires independent builds on two machines; same-host repeatability is partial evidence.",
        }

    def _run(self, argv: tuple[str, ...], *, cwd: Path) -> None:
        try:
            subprocess.run(
                argv,
                cwd=cwd,
                check=True,
                timeout=cast(int, self.settings["process_timeout_seconds"]),
                env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ModelRefused("build_failed") from error

    def check_dependencies(self, paths: tuple[Path, ...]) -> None:
        deny = object_value(self.store.read_json(self.root / "policy/deny-packages.json")["packages"])
        for path in paths:
            document = self.store.read_json(path) if path.suffix == ".json" else None
            text = json.dumps(document) if document else path.read_text(encoding="utf-8")
            for package in deny:
                if f'name = "{package}"' in text:
                    raise ModelRefused("training_dependency_refused")
