from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory

from crew_health_bench.adapters.documents import BenchSettings, decode_json
from crew_health_bench.adapters.isolation import generation_environment
from crew_health_bench.application.ports import AtomicArtifacts
from crew_health_bench.domain.documents import integer, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import HealthBenchVisible
from crew_health_bench.domain.model import ModelReleaseRef
from crew_health_bench.domain.runs import Generation
from crew_health_bench.domain.timing import elapsed


class IsolatedModelGenerator:
    def __init__(
        self,
        *,
        config_dir: Path,
        release_path: Path,
        release: ModelReleaseRef,
        schema: Path,
        base_url: str,
        settings: BenchSettings,
        environment: Mapping[str, str],
        grader_key_env: str,
        clock: Clock,
        ids: IdFactory,
        store: AtomicArtifacts,
        run_id: str,
        cases: Mapping[str, HealthBenchVisible],
    ) -> None:
        self.config_dir, self.release_path, self.release, self.schema = (
            config_dir,
            release_path,
            release,
            schema,
        )
        self.base_url, self.settings = base_url, settings
        self.environment = generation_environment(environment, grader_key_env=grader_key_env)
        self.clock, self.ids, self.store, self.run_id, self.cases = clock, ids, store, run_id, cases

    def _worker(self, case: HealthBenchVisible) -> dict[str, object]:
        timeout = min(self.release.turn_timeout.value, self.settings.quantity("bench.case.deadline").value)
        body = canonical_json(
            {
                "prompt": [{"role": role, "content": content} for role, content in case.messages],
                "timeout": {"value": float(timeout), "unit": "s"},
            }
        )
        maximum = self.settings.integer("bench.store.max_line_bytes")
        if len(body) > maximum:
            raise BenchError("case_invalid")
        arguments = [
            sys.executable,
            "-m",
            "crew_health_bench.api.model_worker",
            "--config-dir",
            str(self.config_dir),
            "--model-release",
            str(self.release_path),
            "--schema",
            str(self.schema),
            "--base-url",
            self.base_url,
        ]
        try:
            process = subprocess.run(
                arguments,
                input=body,
                capture_output=True,
                env=self.environment,
                timeout=float(self.settings.quantity("bench.case.deadline").value),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise BenchError("interrupted", exit_code=7) from error
        except OSError as error:
            raise BenchError("target_unreadable", exit_code=6) from error
        if len(process.stdout) > maximum or len(process.stderr) > maximum:
            raise BenchError("interrupted", exit_code=7)
        if process.returncode != 0:
            try:
                reason = decode_json(process.stderr).get("reason")
            except BenchError as error:
                raise BenchError("interrupted", exit_code=7) from error
            if reason in ("model_identity_mismatch", "drift"):
                raise BenchError(text(reason))
            raise BenchError("interrupted", exit_code=7)
        return decode_json(process.stdout)

    def generate(self, case_id: str, repeat: int) -> Generation:
        if case_id not in self.cases:
            raise BenchError("case_invalid")
        clock = self.clock
        started, mark = clock.now(), (clock.process_epoch(), clock.monotonic_ns())
        result = self._worker(self.cases[case_id])
        if result.get("model_release") != self.release.projection:
            raise BenchError("drift")
        reference = Path(self.run_id) / "outputs" / self.ids.new("benchoutput")
        raw = canonical_json(result)
        self.store.write_atomic(reference, raw)
        return Generation(
            case_id,
            repeat,
            text(result["status"]),
            started,
            clock.now(),
            clock.process_epoch(),
            elapsed(mark, clock),
            sha256(raw).hexdigest(),
            reference.as_posix(),
            reference.as_posix(),
            integer(result["model_calls"]),
        )
