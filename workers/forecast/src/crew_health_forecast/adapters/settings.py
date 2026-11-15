"""Validated process settings, independent of clinical policy."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SettingsInvalid(ValueError):
    pass


def default_policy_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "forecast-policy.json"


def _integer(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get("CREW_HEALTH_FORECAST_" + name, str(default))
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise SettingsInvalid("invalid_" + name.lower())
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsInvalid("invalid_" + name.lower()) from exc
    if not low <= value <= high:
        raise SettingsInvalid("invalid_" + name.lower())
    return value


def _path(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get("CREW_HEALTH_FORECAST_" + name)
    if raw is None:
        return None
    if not raw.strip() or "\x00" in raw:
        raise SettingsInvalid("invalid_" + name.lower())
    return Path(raw)


@dataclass(frozen=True)
class WorkerSettings:
    host: str = "127.0.0.1"
    port: int = 8094
    token_file: Path | None = None
    weights_dir: Path | None = None
    policy_path: Path = default_policy_path()
    max_body_bytes: int = 65536
    max_inflight: int = 1
    timeout_ms: int = 30000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host
            or self.host.strip() != self.host
            or "\x00" in self.host
        ):
            raise SettingsInvalid("invalid_host")
        for name, low, high in (
            ("port", 1, 65535),
            ("max_body_bytes", 1024, 1048576),
            ("max_inflight", 1, 4),
            ("timeout_ms", 100, 120000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise SettingsInvalid("invalid_" + name)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerSettings:
        source = os.environ if env is None else env
        return cls(
            host=source.get("CREW_HEALTH_FORECAST_HOST", "127.0.0.1"),
            port=_integer(source, "PORT", 8094, 1, 65535),
            token_file=_path(source, "TOKEN_FILE"),
            weights_dir=_path(source, "WEIGHTS_DIR"),
            policy_path=_path(source, "POLICY") or default_policy_path(),
            max_body_bytes=_integer(source, "MAX_BODY_BYTES", 65536, 1024, 1048576),
            max_inflight=_integer(source, "MAX_INFLIGHT", 1, 1, 4),
            timeout_ms=_integer(source, "TIMEOUT_MS", 30000, 100, 120000),
        )

    def validate_bind(self, container_network: bool) -> None:
        if self.host not in ("127.0.0.1", "::1") and not container_network:
            raise SettingsInvalid("non_loopback_requires_container_network")

    def load_token(self) -> str | None:
        if self.token_file is None:
            return None
        try:
            # A credential must fit within the same process input budget as a request.
            with self.token_file.open(encoding="utf-8") as stream:
                token = stream.read(self.max_body_bytes + 1)
            if len(token) > self.max_body_bytes:
                return None
            token = token.strip()
            return token if token and not any(char.isspace() for char in token) else None
        except OSError, UnicodeError:
            return None
