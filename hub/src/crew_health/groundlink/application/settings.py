"""SYSTEM 5.11: finite operational bounds, supplied by the hub."""

from dataclasses import dataclass
from decimal import Decimal

from crew_health.kernel.settings import HubSettings, SettingSpec


def groundlink_setting_specs() -> tuple[SettingSpec, ...]:
    rows = (
        (
            "bundle.max_inflight",
            1,
            1024,
            "SYSTEM 5.11 bundle family: bundles handed to their owner in one agent pass",
        ),
        ("bundle.retry_max", 0, 32, "SYSTEM 3: exponent ceiling of the retry backoff"),
        ("bundle.retry_backoff_ms", 1, 60000, "SYSTEM 3: first retry delay"),
        (
            "bundle.renewal_batch",
            1,
            10000,
            "SYSTEM 5.11: rows one agent pass scans for arrivals and inbound items",
        ),
        (
            "groundlink.backoff_cap_ms",
            1000,
            600000,
            "SYSTEM 3: the ground link agent backs off to 60 s, never gives up",
        ),
        ("groundlink.port_timeout_ms", 1, 60000, "SYSTEM 3: bound on each owner lifecycle call"),
        ("groundlink.agent_poll_ms", 10, 600000, "SYSTEM 3: bounded interval between agent passes"),
        ("groundlink.max_reply_bytes", 1, 65536, "SYSTEM 5.11: UTF-8 bound on an inbound ground reply"),
        ("groundlink.history_rows", 1, 10000, "SYSTEM 5.11: link history rows read in one query"),
        (
            "groundlink.subscriber_max_attempts",
            1,
            20,
            "SYSTEM 5.11: deliveries of one event before it is quarantined",
        ),
        (
            "groundlink.history_retention_rows",
            100,
            1000000,
            "SYSTEM 5.11: link history rows kept; older rows are pruned",
        ),
    )
    return tuple(
        SettingSpec(k, "integer", None, Decimal(lo), Decimal(hi), None, basis) for k, lo, hi, basis in rows
    ) + (
        SettingSpec(
            "groundlink.agent_enabled",
            "boolean",
            None,
            Decimal(0),
            Decimal(1),
            None,
            "SYSTEM 3 runs the agent as its own process; true runs its loop inside the hub process",
        ),
    )


@dataclass(frozen=True, slots=True)
class GroundLinkSettings:
    max_inflight: int
    retry_max: int
    retry_backoff_ms: int
    renewal_batch: int
    backoff_cap_ms: int
    port_timeout_ms: int
    agent_poll_ms: int
    max_reply_bytes: int
    history_rows: int
    agent_enabled: bool
    subscriber_max_attempts: int
    history_retention_rows: int

    @classmethod
    def read(cls, settings: HubSettings) -> GroundLinkSettings:
        return cls(
            max_inflight=settings.integer("bundle.max_inflight"),
            retry_max=settings.integer("bundle.retry_max"),
            retry_backoff_ms=settings.integer("bundle.retry_backoff_ms"),
            renewal_batch=settings.integer("bundle.renewal_batch"),
            backoff_cap_ms=settings.integer("groundlink.backoff_cap_ms"),
            port_timeout_ms=settings.integer("groundlink.port_timeout_ms"),
            agent_poll_ms=settings.integer("groundlink.agent_poll_ms"),
            max_reply_bytes=settings.integer("groundlink.max_reply_bytes"),
            history_rows=settings.integer("groundlink.history_rows"),
            agent_enabled=settings.boolean("groundlink.agent_enabled"),
            subscriber_max_attempts=settings.integer("groundlink.subscriber_max_attempts"),
            history_retention_rows=settings.integer("groundlink.history_retention_rows"),
        )
