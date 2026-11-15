"""chc-medqa: model validation of a general medical knowledge proxy (MedQA); never clinical validation.

chc-medqa fetch
chc-medqa probe       (--binary B --weights W --template T --port P | --stand-in URL)
chc-medqa preregister --n N
chc-medqa run         (--binary B --weights W --template T --port P | --stand-in URL)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from crew_health_bench.domain.errors import BenchError
from crew_health_bench.medqa import dataset, run
from crew_health_bench.medqa.config import ProxyConfig, load_config
from crew_health_bench.medqa.domain import McqItem
from crew_health_bench.medqa.model import ChatClient, ModelIdentity, ModelServer, server_argv, verify_pins

_BENCH = Path(__file__).resolve().parents[3]


class Arguments(argparse.Namespace):
    command: str
    config: Path
    cache: Path
    n: int
    binary: Path | None
    weights: Path | None
    template: Path | None
    port: int
    stand_in: str | None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chc-medqa", description=__doc__)
    parser.add_argument("--config", type=Path, default=_BENCH / "config/medqa-proxy.json")
    parser.add_argument("--cache", type=Path, default=_BENCH / "runs/medqa-proxy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    pre = sub.add_parser("preregister")
    pre.add_argument("--n", type=int, required=True)
    for name in ("probe", "run"):
        command = sub.add_parser(name)
        command.add_argument("--binary", type=Path)
        command.add_argument("--weights", type=Path)
        command.add_argument("--template", type=Path)
        command.add_argument("--port", type=int, default=0)
        command.add_argument("--stand-in", dest="stand_in")
    return parser


def _items(config: ProxyConfig, cache: Path) -> dict[str, McqItem]:
    path = dataset.dataset_path(cache / "dataset", config.dataset)
    return dataset.load(path, config.dataset, max_line_bytes=config.max_line_bytes)


class _Session(AbstractContextManager["_Session"]):
    """Either a pinned llama-server it owns (always stopped on exit) or a loopback stand-in."""

    def __init__(self, args: Arguments, config: ProxyConfig) -> None:
        self.config = config
        self.server: ModelServer | None = None
        self.identity: ModelIdentity | None = None
        if args.stand_in is not None:
            if not args.stand_in.startswith("http://127.0.0.1:"):
                raise BenchError("stand_in_not_loopback", exit_code=2)
            self.base_url = args.stand_in
            return
        if (
            args.binary is None
            or args.weights is None
            or args.template is None
            or not 1024 <= args.port <= 65535
        ):
            raise BenchError("model_arguments_missing", exit_code=2)
        self.identity = verify_pins(args.binary, args.weights, args.template, config.model)
        self.base_url = f"http://127.0.0.1:{args.port}"
        argv = server_argv(args.binary, args.weights, args.template, config.runtime, args.port)
        self._argv, self._log = argv, args.cache / "llama-server.log"

    def __enter__(self) -> _Session:
        if self.identity is not None:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            self.server = ModelServer(self._argv, self.base_url, self.config.runtime, self._log)
            try:
                self.server.wait_ready()
            except BaseException:
                self.server.close()
                raise
        return self

    def __exit__(self, *_: object) -> None:
        if self.server is not None:
            self.server.close()

    def client(self) -> ChatClient:
        return ChatClient(
            self.base_url,
            self.config.generation,
            connect_timeout_s=self.config.connect_timeout_s,
            max_chars=self.config.max_response_chars,
        )


def _guarded[T](session: _Session, body: Callable[[_Session], T]) -> T:
    with session:
        return body(session)


def _probe(args: Arguments, config: ProxyConfig) -> dict[str, object]:
    items = _items(config, args.cache)
    sample = run.make_sample(config, items, 1)
    rows = _guarded(_Session(args, config), lambda s: run.probe(s.client(), config, items, sample))
    return {"warmup": rows}


def _run(args: Arguments, config: ProxyConfig) -> dict[str, object]:
    items = _items(config, args.cache)
    sample = run.read_preregistration(args.cache / "preregistration.json", config, items)
    results = args.cache / "results.jsonl"
    started = run.now()
    session = _Session(args, config)
    outcomes = _guarded(session, lambda s: run.generate(s.client(), config, items, sample, results))
    document = run.report(
        config, sample, outcomes, identity=session.identity, stand_in=args.stand_in,
        results=results, wall_seconds=run.now() - started,
    )  # fmt: skip
    (args.cache / "report.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def execute(args: Arguments) -> dict[str, object]:
    config = load_config(args.config)
    if args.command == "fetch":
        path = dataset.fetch(args.cache / "dataset", config.dataset, timeout_s=120)
        return {"path": str(path), "sha256": config.dataset.sha256}
    if args.command == "preregister":
        sample = run.make_sample(config, _items(config, args.cache), args.n)
        run.write_preregistration(args.cache / "preregistration.json", config, sample)
        return {"n": sample.n, "seed": sample.seed, "sample_sha256": sample.sha256}
    if args.command == "probe":
        return _probe(args, config)
    return _run(args, config)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv, namespace=Arguments())
    try:
        document = execute(args)
    except BenchError as error:
        print(json.dumps({"error": error.reason, "detail": error.detail}), file=sys.stderr)
        return error.exit_code
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
