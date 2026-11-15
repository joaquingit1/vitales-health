from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid7

from crew_health.kernel.ids import KernelIdFactory
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.adapters.store import FileArtifactStore
from crew_health_bench.adapters.vault import AgeVault
from crew_health_bench.adapters.hub_system import scan_bound
from crew_health_bench.adapters.leak_scanner import LeakScanner
from crew_health_bench.adapters.partitions import verify_partitions
from crew_health_bench.api import case_commands
from crew_health_bench.domain.claims import ClaimsPolicy, lint_claims
from crew_health_bench.domain.documents import object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.leak import Fingerprint

_BOOTSTRAP_BUFFER_BYTES = 1 << 16


class Arguments(argparse.Namespace):
    config_dir: Path
    group: str
    command: str
    files: list[Path]
    fingerprints: Path
    target: list[list[str]]
    cases: list[str]
    hidden_dir: Path | None
    salt: Path
    fp_salt: str
    root: Path | None
    corpus: Path | None
    variant: str | None
    manifest: Path | None
    run_id: str | None
    model_release: Path | None
    base_url: str | None
    preregistration: Path | None
    grader_url: str | None
    published: Path | None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chc-bench")
    parser.add_argument("--config-dir", type=Path, required=True)
    groups = parser.add_subparsers(dest="group", required=True)
    claims = groups.add_parser("lint").add_subparsers(dest="command", required=True).add_parser("claims")
    claims.add_argument("files", type=Path, nargs="+")
    leak = groups.add_parser("leak").add_subparsers(dest="command", required=True).add_parser("scan")
    leak.add_argument("--fingerprints", type=Path, required=True)
    leak.add_argument("--target", nargs="+", action="append", required=True)
    vault = groups.add_parser("vault").add_subparsers(dest="command", required=True)
    vault.add_parser("seal").add_argument("files", type=Path, nargs="+")
    vault.add_parser("verify").add_argument("cases", nargs="+")
    cases = groups.add_parser("cases").add_subparsers(dest="command", required=True)
    validate = cases.add_parser("validate")
    validate.add_argument("files", type=Path, nargs="+")
    validate.add_argument("--hidden-dir", type=Path)
    partitions = groups.add_parser("partitions").add_subparsers(dest="command", required=True)
    freeze = partitions.add_parser("freeze")
    freeze.add_argument("--cases", dest="files", type=Path, nargs="+", required=True)
    freeze.add_argument("--hidden-dir", type=Path, required=True)
    freeze.add_argument("--salt", type=Path, required=True)
    freeze.add_argument("--fp-salt", required=True)
    verify = partitions.add_parser("verify")
    verify.add_argument("--root", type=Path)
    healthbench = groups.add_parser("healthbench").add_subparsers(dest="command", required=True)
    for name in ("fetch", "preregister", "plan", "generate", "grade", "report"):
        command = healthbench.add_parser(name)
        command.set_defaults(files=[], corpus=None, variant=None, manifest=None, run_id=None,
                             model_release=None, base_url=None, preregistration=None, salt=None,
                             grader_url=None, published=None)
        command.add_argument("--root", type=Path, required=True)
        if name == "fetch":
            command.add_argument("--variant", required=True)
            command.add_argument("files", type=Path, nargs="+")
            continue
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--model-release", type=Path, required=True)
        command.add_argument("--preregistration", type=Path)
        if name in ("preregister", "generate"):
            command.add_argument("--base-url", required=True)
        if name == "plan":
            command.add_argument("--manifest", type=Path, required=True)
        elif name != "preregister":
            command.add_argument("--run-id", required=True)
        if name in ("grade", "report"):
            command.add_argument("--grader-url", required=True)
        if name == "report":
            command.add_argument("--salt", type=Path, required=True)
            command.add_argument("--published", type=Path, required=True)
    return parser


def _read_text(path: Path, *, max_file_bytes: int) -> str:
    if path.is_symlink() or not path.is_file():
        raise BenchError("target_unreadable", instance=str(path), exit_code=6)
    try:
        with path.open("rb") as source:
            data = source.read(max_file_bytes + 1)
        if len(data) > max_file_bytes:
            raise BenchError("target_unreadable", instance=str(path), exit_code=6)
        return data.decode("utf-8")
    except OSError as error:
        raise BenchError("target_unreadable", instance=str(path), exit_code=6) from error
    except UnicodeError as error:
        raise BenchError("case_invalid", instance=str(path)) from error


def _fingerprints(path: Path, limit: int) -> tuple[str, tuple[Fingerprint, ...]]:
    data = read_json(path, max_file_bytes=limit)
    rows = data.get("cases")
    if not isinstance(rows, list) or not rows:
        raise BenchError("case_invalid", instance=str(path))
    result: list[Fingerprint] = []
    for item in rows:
        row = object_map(item)
        result.append(
            Fingerprint(
                text(row.get("case_ref")),
                frozenset(text_list(row.get("shingles"))),
                text_list(row.get("canaries")),
            )
        )
    if any(not item.shingles and not item.canaries for item in result):
        raise BenchError("case_invalid", instance=str(path))
    return text(data.get("fp_salt")), tuple(result)


def _scan(
    path: Path,
    targets: list[list[str]],
    *,
    config_dir: Path,
    limit: int,
) -> list[dict[str, object]]:
    fp_salt, fingerprints = _fingerprints(path, limit)
    policy = load_document(config_dir / "partition-policy.json", max_file_bytes=limit)
    threshold = policy["leak_jaccard"]
    if not isinstance(threshold, (float, int)) or isinstance(threshold, bool):
        raise BenchError("case_invalid")
    parsed: list[tuple[str, Path]] = []
    for target in targets:
        if len(target) < 2:
            raise BenchError("case_invalid")
        parsed.extend((target[0], Path(path)) for path in target[1:])
    return LeakScanner(max_files=scan_bound(config_dir.resolve().parents[2], max_file_bytes=limit),
                       max_file_bytes=limit).scan(tuple(parsed), fingerprints, fp_salt=fp_salt,
                                                  leak_jaccard=threshold)


def _vault(args: Arguments, config_dir: Path, settings: BenchSettings, limit: int) -> None:
    policy = load_document(config_dir / "partition-policy.json", max_file_bytes=limit)
    recipients = text_list(object_map(policy["vault"])["recipients"])
    count = len(args.files) if args.command == "seal" else len(args.cases)
    bound = max(count, len(recipients), 1)
    repository_root = config_dir.resolve().parents[2]
    root = repository_root / "eval/vault"
    with FileArtifactStore(
        root, max_line_bytes=settings.integer("bench.store.max_line_bytes"), max_records=bound
    ) as store:
        vault = AgeVault(
            root=root,
            repository_root=repository_root,
            store=store,
            max_file_bytes=settings.integer("bench.store.max_line_bytes"),
            max_files=bound,
            environment={},
        )
        if args.command == "seal":
            vault.seal(args.files, recipients)
        else:
            for case_id in args.cases:
                vault.verify(case_id)



def _cases(args: Arguments, config_dir: Path, settings: BenchSettings, limit: int) -> None:
    ids = KernelIdFactory(new_uuid_hex=lambda: uuid7().hex)
    if args.group == "cases":
        case_commands.validate(args.files, config_dir=config_dir, settings=settings, ids=ids,
                               hidden_dir=args.hidden_dir)
    elif args.command == "verify":
        verify_partitions(args.root or config_dir.parent, max_file_bytes=limit)
    else:
        if args.hidden_dir is None:
            raise BenchError("case_invalid")
        case_commands.freeze(args.files, config_dir=config_dir, settings=settings, ids=ids,
                             hidden_dir=args.hidden_dir, salt_path=args.salt, fp_salt=args.fp_salt)

def run(argv: Sequence[str]) -> int:
    args = Arguments()
    _parser().parse_args(argv, namespace=args)
    config_dir = Path(args.config_dir)
    try:
        settings = BenchSettings(
            load_document(config_dir / "bench-settings.json", max_file_bytes=_BOOTSTRAP_BUFFER_BYTES)
        )
        limit = settings.integer("bench.leak.max_file_bytes")
        findings: list[dict[str, object]]
        if args.group == "healthbench":
            from crew_health_bench.api.healthbench_commands import HealthBenchCommands, Options

            if args.root is None:
                raise BenchError("case_invalid")
            handler = HealthBenchCommands(config_dir, settings, Options(
                command=args.command, root=args.root, corpus=args.corpus or args.root, variant=args.variant,
                files=tuple(args.files), manifest=args.manifest, run_id=args.run_id, model_release=args.model_release,
                base_url=args.base_url, preregistration=args.preregistration, salt=args.salt,
                grader_url=args.grader_url, published=args.published))
            result = (handler.fetch() if args.command == "fetch" else handler.preregister()
                      if args.command == "preregister" else handler.run())
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.group == "lint":
            policy = ClaimsPolicy.from_document(
                load_document(config_dir / "claims-policy.json", max_file_bytes=limit)
            )
            findings = [
                {**asdict(item), "path": str(path)}
                for path in args.files
                for item in lint_claims(_read_text(path, max_file_bytes=limit), policy)
            ]
        elif args.group in ("cases", "partitions"):
            _cases(args, config_dir, settings, limit)
            findings = []
        elif args.group == "vault":
            _vault(args, config_dir, settings, limit)
            findings = []
        else:
            findings = _scan(args.fingerprints, args.target, config_dir=config_dir, limit=limit)
        print(json.dumps({"findings": findings}, sort_keys=True))
        return 3 if findings else 0
    except BenchError as error:
        print(
            json.dumps(
                {
                    "type": "urn:crew-health:bench:" + error.reason,
                    "title": error.reason,
                    "instance": error.instance,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return error.exit_code


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))
