"""Loading of the worker's documents at startup: the quality policy and the operational bounds. Either one missing,
corrupt or out of bounds refuses to start the worker (PLAN C3, AGENTS 3.3); nothing falls back to a default."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from crew_health_camera.application.model import WorkerBounds
from crew_health_camera.domain.policy import PolicyInvalid, QualityPolicy, parse_policy

PACKAGE = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PACKAGE / "policy" / "camera-quality.json"
DEFAULT_SETTINGS = PACKAGE / "policy" / "worker-settings.json"
CEILINGS: dict[str, tuple[int, int]] = {
    "max_acquisitions": (1, 256),
    "max_frames": (2, 100_000),
    "max_devices": (1, 16),
    "delivery_backoff_ms": (1, 60_000),
    "delivery_backoff_max_ms": (1, 600_000),
    "max_ttl_ms": (1, 3_600_000),
    "hub_timeout_ms": (1, 60_000),
    "runner_idle_ms": (1, 60_000),
    "max_body_bytes": (1_024, 1_048_576),
    "camera_timeout_ms": (1, 60_000),
}


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    bounds: WorkerBounds
    hub_timeout_ms: int
    runner_idle_ms: int
    max_body_bytes: int
    camera_timeout_ms: int


def _document(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PolicyInvalid(str(path)) from error
    if not isinstance(value, dict):
        raise PolicyInvalid(str(path))
    return value


def load_policy(path: Path = DEFAULT_POLICY) -> QualityPolicy:
    return parse_policy(_document(path))


def load_settings(path: Path = DEFAULT_SETTINGS) -> WorkerSettings:
    document = _document(path)
    if set(document) != set(CEILINGS):
        raise PolicyInvalid("/settings")
    values: dict[str, int] = {}
    for key, (low, high) in CEILINGS.items():
        entry = document[key]
        if not isinstance(entry, dict) or not isinstance(entry.get("basis"), str) or not entry["basis"]:
            raise PolicyInvalid("/" + key)
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise PolicyInvalid("/" + key + "/value")
        values[key] = value
    bounds = WorkerBounds(
        max_acquisitions=values["max_acquisitions"],
        max_frames=values["max_frames"],
        max_devices=values["max_devices"],
        delivery_backoff_ms=values["delivery_backoff_ms"],
        delivery_backoff_max_ms=values["delivery_backoff_max_ms"],
        max_ttl_ms=values["max_ttl_ms"],
    )
    if bounds.delivery_backoff_max_ms < bounds.delivery_backoff_ms:
        raise PolicyInvalid("/delivery_backoff_max_ms/value")
    return WorkerSettings(
        bounds,
        values["hub_timeout_ms"],
        values["runner_idle_ms"],
        values["max_body_bytes"],
        values["camera_timeout_ms"],
    )
