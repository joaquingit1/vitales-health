"""E17 development tooling. Outputs references and safe codes, never inference content."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from crew_health.model.adapters.activation import ActivationService
from crew_health.model.adapters.build_pipeline import BuildPipeline
from crew_health.model.adapters.isolation import refuse_holdout
from crew_health.model.adapters.manifest_store import ManifestStore, file_hash
from crew_health.model.adapters.release_service import ReleaseService
from crew_health.model.adapters.runtime_probe import RuntimeIdentity
from crew_health.model.domain.release import ModelRefused


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="crew-health-model")
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--data-dir", type=Path, required=True)
    result.add_argument("--profile", choices=("flight", "demo", "eval"), default="flight")
    actions = result.add_subparsers(dest="action", required=True)
    for name in ("verify", "activate"):
        command = actions.add_parser(name)
        command.add_argument("release_id")
    actions.add_parser("rollback")
    pin = actions.add_parser("pin")
    pin.add_argument("revision")
    pin.add_argument("--name", required=True)
    fetch = actions.add_parser("fetch")
    fetch.add_argument("directory", type=Path)
    convert = actions.add_parser("convert")
    convert.add_argument("source_tree", type=Path)
    convert.add_argument("checkpoint", type=Path)
    convert.add_argument("output", type=Path)
    convert.add_argument("--python", type=Path, required=True)
    quant = actions.add_parser("quantize")
    quant.add_argument("source", type=Path)
    quant.add_argument("output", type=Path)
    quant.add_argument("--binary", type=Path, required=True)
    quant.add_argument("--kind", choices=("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"), required=True)
    reproduce = actions.add_parser("reproduce")
    reproduce.add_argument("first", type=Path)
    reproduce.add_argument("second", type=Path)
    reproduce.add_argument("--first-machine", required=True)
    reproduce.add_argument("--second-machine", required=True)
    equivalence = actions.add_parser("verify-equivalence")
    equivalence.add_argument("checkpoint", type=Path)
    equivalence.add_argument("artifact", type=Path)
    equivalence.add_argument("output", type=Path)
    equivalence.add_argument("--python", type=Path, required=True)
    tuning = actions.add_parser("tune-input")
    tuning.add_argument("input", type=Path)
    tuning.add_argument("--dev", type=Path, required=True)
    tuning.add_argument("--holdout", type=Path, required=True)
    bundle = actions.add_parser("bundle")
    bundle.add_argument("release_id")
    bundle.add_argument("destination", type=Path)
    scan = actions.add_parser("scan-dependencies")
    scan.add_argument("locks", nargs="+", type=Path)
    probe = actions.add_parser("probe")
    probe.add_argument("release_id")
    probe.add_argument("--port", type=int, required=True)
    probe.add_argument("--timeout", type=float, default=1)
    release = actions.add_parser("release")
    releases = release.add_subparsers(dest="release_action", required=True)
    build = releases.add_parser("build")
    build.add_argument("manifest", type=Path)
    promotion = releases.add_parser("promote")
    promotion.add_argument("release_id")
    promotion.add_argument("gate", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = ManifestStore(args.root, args.data_dir, profile=args.profile)
    try:
        action = args.action
        if action == "release":
            service = ReleaseService(store)
            result: object = (
                service.build(args.manifest)
                if args.release_action == "build"
                else service.promote(args.release_id, args.gate)
            )
        elif action == "verify":
            release = store.get(args.release_id)
            result = {
                "release_id": release.release_id,
                "manifest_sha256": file_hash(args.root / "releases" / (args.release_id + ".json")),
            }
        elif action in ("activate", "rollback"):
            # Flight activation additionally requires the signed package/audited activation binding from E20/E01.
            if args.profile == "flight":
                raise ModelRefused("signed_audited_activation_binding_unavailable")
            activation = ActivationService(store, now=lambda: datetime.now(UTC))
            result = (
                activation.activate(args.release_id, actor_id="local_eval_operator")
                if action == "activate"
                else activation.rollback(actor_id="local_eval_operator")
            )
        elif action == "probe":
            key = (args.data_dir / "models/runtime.key").read_text(encoding="utf-8").strip()
            measured = RuntimeIdentity(store, port=args.port, key=key, max_bytes=store.max_bytes).probe(
                args.release_id, timeout_s=args.timeout, force_hash=True
            )
            result = {
                "healthy": measured.healthy,
                "identity_ok": measured.identity_ok,
                "adapters_empty": measured.adapters_empty,
                "detail_code": measured.detail_code,
                "claim": "Runtime identity only. This is not a completed P1-P8 model qualification report.",
            }
        else:
            if args.profile == "flight":
                raise ModelRefused("build_operations_not_available_in_flight")
            build = BuildPipeline(args.root)
            if action == "pin":
                result = build.pin(args.revision, name=args.name)
            elif action == "fetch":
                result = build.fetch(args.root / "pins/huatuogpt-3-9b.json", args.directory)
            elif action == "convert":
                result = build.convert(args.source_tree, args.checkpoint, args.output, python=args.python)
            elif action == "quantize":
                result = build.quantize(args.source, args.output, binary=args.binary, kind=args.kind)
            elif action == "verify-equivalence":
                result = build.verify_equivalence(
                    args.checkpoint, args.artifact, args.output, python=args.python
                )
            elif action == "tune-input":
                path = refuse_holdout(args.input, dev=args.dev, holdout=args.holdout)
                if not path.is_file() or path.stat().st_size > store.max_bytes:
                    raise ModelRefused("development_input_bound")
                result = {"partition": "dev", "sha256": file_hash(path)}
            elif action == "bundle":
                result = ReleaseService(store).bundle(args.release_id, args.destination)
            elif action == "reproduce":
                result = build.reproduce(
                    args.first,
                    args.second,
                    first_machine=args.first_machine,
                    second_machine=args.second_machine,
                )
            else:
                build.check_dependencies(tuple(cast(list[Path], args.locks)))
                result = {"dependency_scan": "pass"}
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ModelRefused, OSError, ValueError, KeyError) as error:
        code = str(error) if isinstance(error, ModelRefused) else "artifact_unavailable"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2
