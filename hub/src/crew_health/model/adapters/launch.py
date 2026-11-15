from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import cast

from crew_health.model.adapters.manifest_store import ManifestStore, file_hash
from crew_health.model.application.ports import LaunchSpec
from crew_health.model.domain.release import ModelRefused, object_value

# Mechanism allowlist, never configurable to admit adapters, downloads, arbitrary metadata or extra paths.
ALLOWED = frozenset(
    {
        "--model",
        "--alias",
        "--host",
        "--port",
        "--api-key-file",
        "--ctx-size",
        "--parallel",
        "--cache-type-k",
        "--cache-type-v",
        "--threads",
        "--n-gpu-layers",
        "--fit",
        "--jinja",
        "--chat-template-file",
        "--reasoning-format",
        "--offline",
        "--no-webui",
        "--log-disable",
    }
)


def validate_flags(argv: tuple[str, ...], captured_help: str) -> None:
    known = frozenset(re.findall(r"--[a-z][a-z0-9-]*", captured_help))
    for argument in argv[1:]:
        if argument.startswith("-"):
            if "=" in argument:
                raise ModelRefused("flag_not_allowed")
            flag = argument.split("=", 1)[0]
            if flag not in ALLOWED:
                raise ModelRefused("flag_not_allowed")
            if flag not in known:
                raise ModelRefused("flag_unknown_to_build")


class LaunchBuilder:
    def __init__(
        self,
        store: ManifestStore,
        *,
        binary: Path,
        port: int,
        captured_help: str,
        memory_budget_bytes: int,
        cpu_budget: int,
        nice: int,
    ) -> None:
        if not (
            1 <= port <= 65535
            and 1 <= memory_budget_bytes <= 7500000000
            and 1 <= cpu_budget <= 6
            and 0 <= nice <= 19
        ):
            raise ModelRefused("launch_invalid")
        self.store, self.binary, self.port, self.help = store, binary, port, captured_help
        self.memory_budget_bytes, self.cpu_budget, self.nice = memory_budget_bytes, cpu_budget, nice

    def launch_spec(self, release_id: str) -> LaunchSpec:
        document = self.store.get(release_id).document
        if document["mode"] != "live":
            raise ModelRefused("stub_has_no_process")
        runtime = object_value(document["runtime"])
        pin = self.store.read_json(self.store.root / "pins/llama-cpp.json")
        if (
            not self.binary.is_file()
            or file_hash(self.binary) != pin.get("binary_sha256")
            or runtime["commit"] != pin["commit"]
            or runtime["build"] != pin["build"]
            or hashlib.sha256(self.help.encode("utf-8")).hexdigest() != pin["help_sha256"]
        ):
            raise ModelRefused("runtime_binary_mismatch")
        weights = object_value(document["weights"])
        path = self.store.data_dir / "models" / str(weights["file_name"])
        if not path.is_file() or path.is_symlink() or file_hash(path) != weights["weights_sha256"]:
            raise ModelRefused("weights_hash_mismatch")
        if path.stat().st_mode & 0o222:
            raise ModelRefused("weights_writable")
        if runtime["engine"] != "llama.cpp":
            raise ModelRefused("launch_invalid")
        template = self.store.root / "templates" / (str(document["chat_template_sha256"]) + ".jinja")
        key = self.store.data_dir / "models/runtime.key"
        argv = (
            str(self.binary.resolve()),
            "--model",
            str(path.resolve()),
            "--alias",
            release_id,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--api-key-file",
            str(key.resolve()),
            "--ctx-size",
            str(runtime["context_tokens"]),
            "--parallel",
            "1",
            "--cache-type-k",
            str(runtime["kv_cache_type"]),
            "--cache-type-v",
            str(runtime["kv_cache_type"]),
            "--threads",
            str(runtime["threads"]),
            "--n-gpu-layers",
            str(runtime["gpu_layers"]),
            "--fit",
            "off",
            "--jinja",
            "--chat-template-file",
            str(template.resolve()),
            "--reasoning-format",
            "deepseek",
            "--offline",
            "--no-webui",
            "--log-disable",
        )
        validate_flags(argv, self.help)
        return LaunchSpec(
            "model",
            argv,
            {},
            str(self.store.data_dir.resolve()),
            f"http://127.0.0.1:{self.port}/health",
            float(cast(float, runtime["load_timeout_seconds"])),
            self.memory_budget_bytes,
            self.cpu_budget,
            self.nice,
        )
