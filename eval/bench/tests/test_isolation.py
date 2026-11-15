"""T-E18-07, 12, 15: fail closed at evaluation boundaries."""

import json
from pathlib import Path

import pytest
from crew_health.contracts_gen.schemas.context_schema import Context
from crew_health_bench.adapters.isolation import generation_environment, require_evaluation_hub, vault_path
from crew_health_bench.domain.errors import BenchError

pytestmark = [pytest.mark.e18, pytest.mark.trace("INV-E18-03", "INV-E18-10", "V33")]
ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("relative", ["../outside", "/tmp/file", "a/../../outside"])
def test_t_e18_07_path_escape(tmp_path: Path, relative: str) -> None:
    with pytest.raises(BenchError, match="vault_path_escape"):
        vault_path(tmp_path, relative)


def test_t_e18_07_symlink_component(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(BenchError, match="vault_path_escape"):
        vault_path(tmp_path, "link/anything")
    (tmp_path / "real").mkdir()
    assert vault_path(tmp_path, "real/cipher.age") == tmp_path / "real/cipher.age"


def test_t_e18_15_no_identity_or_grader_key_in_generation_environment() -> None:
    environment = {"PATH": "/bin", "CHC_EVAL_IDENTITY": "/private/key", "GRADER_KEY": "secret"}
    assert generation_environment(environment, grader_key_env="GRADER_KEY") == {"PATH": "/bin"}
    assert len(environment) == 3


def test_t_e18_12_live_and_missing_marker_refused(tmp_path: Path) -> None:
    data = json.loads((ROOT / "contracts/api/examples/context/station.json").read_bytes())["value"]
    context = Context.model_validate(data)
    with pytest.raises(BenchError, match="live_hub_refused"):
        require_evaluation_hub(context, tmp_path, run_id="run", max_file_bytes=1048576)
    marker = tmp_path / ".chc-eval-marker.json"
    marker.write_text(
        json.dumps(
            {
                "kind": "evaluation",
                "run_id": "run",
                "fixture_id": "test",
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
    )
    require_evaluation_hub(context, tmp_path, run_id="run", max_file_bytes=1048576)
    with pytest.raises(BenchError):
        require_evaluation_hub(context, tmp_path, run_id="another", max_file_bytes=1048576)
    data["mission_mode"] = "LIVE"
    with pytest.raises(BenchError, match="live_hub_refused"):
        require_evaluation_hub(Context.model_validate(data), tmp_path, run_id="run", max_file_bytes=1048576)


@pytest.mark.parametrize("relative", ["C:/outside", "C:\\outside", "\\\\host\\share\\file", "dir\\file"])
def test_windows_paths_cannot_escape_even_on_posix(tmp_path: Path, relative: str) -> None:
    with pytest.raises(BenchError, match="vault_path_escape"):
        vault_path(tmp_path, relative)


@pytest.mark.trace("T-E18-12", "invariant")
def test_shared_e21_marker_contract_and_unknown_kind_refusal(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from crew_health_harness.adapters.launcher import EvaluationMarker

    data = json.loads((ROOT / "contracts/api/examples/context/station.json").read_bytes())["value"]
    context = Context.model_validate(data)
    marker = EvaluationMarker(kind="evaluation", run_id="run", fixture_id="test",
                              created_at=datetime(2026, 1, 1, tzinfo=UTC))
    path = tmp_path / ".chc-eval-marker.json"
    path.write_text(marker.model_dump_json(), encoding="utf-8")
    require_evaluation_hub(context, tmp_path, run_id="run", max_file_bytes=1048576)
    invalid = marker.model_dump(mode="json")
    invalid["kind"] = "EVALUATION_HUB"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(BenchError, match="live_hub_refused"):
        require_evaluation_hub(context, tmp_path, run_id="run", max_file_bytes=1048576)
