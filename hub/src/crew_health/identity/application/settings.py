"""E02 11 operational settings: registered descriptors read through S5.11, never a second loader.

Values are operational bounds with basis `team default`; they carry no clinical meaning. Durations keep
their UCUM unit in the descriptor and are read as integers in that unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from crew_health.identity.domain.limits import AttemptPolicy
from crew_health.identity.domain.roster import MEMBERS_CEILING
from crew_health.identity.domain.session import SessionLimits
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.units import Unit

BASIS = "team default"

# key, unit, minimum, maximum, default (E02 11).
IDENTITY_SETTINGS: tuple[tuple[str, str | None, int, int, int], ...] = (
    ("security.idle_lock_minutes", "min", 1, 30, 5),
    ("security.enrollment_code_minutes", "min", 1, 30, 5),
    ("security.auth_attempts", None, 1, 20, 5),
    ("security.auth_window_ms", "ms", 1000, 3600000, 300000),
    ("identity.global_auth_attempts", None, 1, 100, 20),
    ("identity.auth_buckets_max", None, 1, 4096, 256),
    ("identity.auth_concurrency", None, 1, 2, 2),
    ("identity.auth_wait_ms", "ms", 1, 3000, 1000),
    ("identity.auth_compute_ms", "ms", 1, 5000, 2000),
    ("identity.argon2_memory_kib", "KiBy", 65536, 65536, 65536),
    ("identity.argon2_iterations", None, 3, 3, 3),
    ("identity.argon2_parallelism", None, 4, 4, 4),
    ("identity.credential_max_bytes", "By", 1, 4096, 1024),
    ("identity.sessions_max", None, 1, 256, 64),
    ("identity.sessions_per_principal", None, 1, 8, 4),
    ("identity.station_lifetime_s", "s", 60, 86400, 86400),
    ("identity.adapter_lifetime_s", "s", 60, 86400, 3600),
    ("identity.roster_max", None, 1, MEMBERS_CEILING, 64),
    ("identity.roster_day_span_max", None, 1, 3660, 366),
    ("identity.page_max", None, 1, 200, 200),
    ("identity.sweep_batch", None, 1, 256, 64),
    ("identity.sweep_interval_ms", "ms", 100, 15000, 1000),
    ("identity.vault_call_timeout_ms", "ms", 1, 5000, 2000),
)


def specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec(
            key, "integer", None if unit is None else Unit(unit), Decimal(low), Decimal(high), None, BASIS
        )
        for key, unit, low, high, _ in IDENTITY_SETTINGS
    )


def defaults() -> dict[str, int]:
    return {key: default for key, _, _, _, default in IDENTITY_SETTINGS}


@dataclass(frozen=True, slots=True)
class Argon2Parameters:
    memory_kib: int
    iterations: int
    parallelism: int


@dataclass(frozen=True, slots=True)
class IdentitySettings:
    limits: SessionLimits
    attempts: AttemptPolicy
    buckets_max: int
    concurrency: int
    wait_ms: int
    compute_ms: int
    argon2: Argon2Parameters
    credential_max_bytes: int
    sessions_max: int
    sessions_per_principal: int
    roster_max: int
    day_span_max: int
    page_max: int
    sweep_batch: int

    @classmethod
    def from_hub(cls, settings: HubSettings) -> IdentitySettings:
        """Refuses to start when storage.max_rows cannot hold one complete read of the current roster.

        That read is the largest identity makes; every other one is bounded below it by its own setting.
        """
        value = settings.integer
        if value("storage.max_rows") <= MEMBERS_CEILING:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        return cls(
            limits=SessionLimits(
                idle_lock=timedelta(minutes=value("security.idle_lock_minutes")),
                station=timedelta(seconds=value("identity.station_lifetime_s")),
                adapter=timedelta(seconds=value("identity.adapter_lifetime_s")),
            ),
            attempts=AttemptPolicy(
                per_identity=value("security.auth_attempts"),
                global_attempts=value("identity.global_auth_attempts"),
                window_ns=value("security.auth_window_ms") * 1_000_000,
            ),
            buckets_max=value("identity.auth_buckets_max"),
            concurrency=value("identity.auth_concurrency"),
            wait_ms=value("identity.auth_wait_ms"),
            compute_ms=value("identity.auth_compute_ms"),
            argon2=Argon2Parameters(
                value("identity.argon2_memory_kib"),
                value("identity.argon2_iterations"),
                value("identity.argon2_parallelism"),
            ),
            credential_max_bytes=value("identity.credential_max_bytes"),
            sessions_max=value("identity.sessions_max"),
            sessions_per_principal=value("identity.sessions_per_principal"),
            roster_max=value("identity.roster_max"),
            day_span_max=value("identity.roster_day_span_max"),
            page_max=value("identity.page_max"),
            sweep_batch=value("identity.sweep_batch"),
        )
