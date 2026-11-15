from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from crew_health.contracts_gen.schemas.common_schema import Id, Instant
from crew_health.contracts_gen.schemas.context_schema import Context
from pydantic import BaseModel, ConfigDict, ValidationError

from crew_health_bench.adapters.documents import read_json
from crew_health_bench.domain.errors import BenchError


def vault_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or PureWindowsPath(relative).drive
        or "\\" in relative
        or ".." in path.parts
        or not path.parts
        or "\0" in relative
    ):
        raise BenchError("vault_path_escape", exit_code=5)
    try:
        current = root.absolute()
        for ancestor in (*reversed(current.parents), current):
            if stat.S_ISLNK(ancestor.lstat().st_mode):
                raise BenchError("vault_path_escape", exit_code=5)
        for component in path.parts:
            current /= component
            if current.is_symlink():
                raise BenchError("vault_path_escape", exit_code=5)
        if not current.resolve().is_relative_to(root.resolve()):
            raise BenchError("vault_path_escape", exit_code=5)
        return current
    except OSError as error:
        raise BenchError("target_unreadable", exit_code=6) from error


def refuse_repository_plaintext(paths: tuple[Path, ...], *, repository: Path) -> None:
    """Holdout case files and hidden parts are read only from outside the repository (design 5.1, M3)."""
    root = repository.resolve()
    inside = sorted(str(path) for path in paths if path.resolve().is_relative_to(root))
    if inside:
        raise BenchError(
            "holdout_plaintext_in_repository",
            exit_code=5,
            detail="holdout plaintext must stay outside the repository; seal it into eval/vault: "
            + ", ".join(inside),
        )


def generation_environment(environment: Mapping[str, str], *, grader_key_env: str) -> dict[str, str]:
    return {
        key: value for key, value in environment.items() if key not in ("CHC_EVAL_IDENTITY", grader_key_env)
    }


def refuse_vault_identity(environment: Mapping[str, str], *, grader_key_env: str) -> None:
    """In-process generation (system-assisted and product tracks) runs only without the vault identity or
    grader key.

    INV-E18-03: the process that runs the system under test can never read a hidden part.
    """
    present = sorted(name for name in ("CHC_EVAL_IDENTITY", grader_key_env) if environment.get(name))
    if present:
        raise BenchError(
            "holdout_key_required",
            exit_code=5,
            detail="generation runs the system under test in this process; unset " + " and ".join(present),
        )


class EvaluationMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["demo", "evaluation"]
    run_id: Id
    fixture_id: Id
    created_at: Instant


def require_evaluation_hub(context: Context, data_dir: Path, *, run_id: str, max_file_bytes: int) -> None:
    if context.mission_mode.root != "SIM":
        raise BenchError("live_hub_refused", exit_code=5)
    marker_path = data_dir / ".chc-eval-marker.json"
    if not marker_path.is_file() or marker_path.is_symlink():
        raise BenchError("live_hub_refused", exit_code=5)
    try:
        marker = EvaluationMarker.model_validate(read_json(marker_path, max_file_bytes=max_file_bytes))
    except ValidationError as error:
        raise BenchError("live_hub_refused", exit_code=5) from error
    if marker.run_id.root != run_id:
        raise BenchError("live_hub_refused", exit_code=5)
