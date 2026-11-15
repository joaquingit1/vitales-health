"""The channel registry (E23 5.3): how this deployment is wired, validated before it is used.

A registry is one JSON document: `{"channels": [...]}`. Each item carries the static fields of SensorChannel (the
runtime fields are the hub's), a `health_url` and, for an override of the catalog's interval or staleness, the
`expected_interval_basis` or `stale_after_basis` that says where the number comes from. The static part is
validated with the generated SensorChannel model; the rest against the active release. An invalid registry is
rejected whole and the last good one stays loaded (AGENTS 3).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from crew_health.contracts_gen.schemas.sensor_channel_schema import SensorChannel
from crew_health.sensors.application.store import RegistryChannel
from crew_health.sensors.domain.channel import ChannelFacts, SensorTypeFacts
from crew_health.sensors.domain.plausibility import registration_policy

_STATIC = (
    "channel_id",
    "sensor_type_id",
    "hazard_id",
    "environment_ids",
    "location",
    "location_label",
    "adapter_id",
    "origin_mode",
    "input_unit",
    "expected_interval_seconds",
    "stale_after_seconds",
    "source_label",
    "dataset",
    "display_note",
    "trusted",
)
_OPTIONAL = ("health_url", "expected_interval_basis", "stale_after_basis")


class RegistryRejected(Exception):
    """The registry document is refused. `findings` are pointers and codes only, never document text."""

    def __init__(self, findings: tuple[str, ...]) -> None:
        super().__init__("; ".join(findings))
        self.findings = findings


def registry_version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _item(index: int, item: object) -> tuple[dict[str, Any], str | None]:
    pointer = f"/channels/{index}"
    if not isinstance(item, dict):
        raise RegistryRejected((pointer + ": not an object",))
    unknown = set(item) - set(_STATIC) - set(_OPTIONAL)
    missing = [key for key in _STATIC if key not in item]
    if unknown or missing:
        raise RegistryRejected((pointer + ": fields " + ",".join(sorted(unknown) + missing),))
    health = item.get("health_url")
    if health is not None and (not isinstance(health, str) or not health):
        raise RegistryRejected((pointer + "/health_url: empty",))
    static = {key: item[key] for key in _STATIC}
    try:
        SensorChannel.model_validate(
            {**static, "last_received_at": None, "last_observed_at": None, "freshness": "never_reported"}
        )
    except ValidationError as error:
        raise RegistryRejected((pointer + ": fails SensorChannel (" + str(error.error_count()) + " errors)",)) from error
    return item, health


def _cross_check(
    index: int, item: dict[str, Any], sensor_type: SensorTypeFacts | None, *, criteria_available: bool
) -> list[str]:
    pointer = f"/channels/{index}"
    if sensor_type is None:
        return [pointer + "/sensor_type_id: not in the active catalog"]
    found: list[str] = []
    if item["hazard_id"] != sensor_type.hazard_id:
        found.append(pointer + "/hazard_id: not the sensor type's hazard")
    if item["input_unit"] != sensor_type.unit and sensor_type.accepted_unit(item["input_unit"]) is None:
        found.append(pointer + "/input_unit: not the catalog unit or an accepted unit")
    for key, catalog_value, basis in (
        ("expected_interval_seconds", sensor_type.expected_interval_seconds, "expected_interval_basis"),
        ("stale_after_seconds", sensor_type.stale_after_seconds, "stale_after_basis"),
    ):
        if item[key] != catalog_value and not item.get(basis):
            found.append(f"{pointer}/{key}: an override needs its basis")
    interval = item["expected_interval_seconds"]
    if interval is not None and item["stale_after_seconds"] <= interval:
        found.append(pointer + "/stale_after_seconds: must exceed the interval or the channel flaps stale")
    if item["origin_mode"] == "REPLAY" and not item["dataset"]:
        found.append(pointer + "/dataset: a REPLAY channel names its dataset")
    policy = registration_policy(item["origin_mode"], criteria_available=criteria_available)
    if not policy.register:
        found.append(pointer + "/origin_mode: refused, " + policy.reason)
    if not item.get("health_url"):
        found.append(pointer + "/health_url: every adapter names its health url")
    return found


def parse_registry(
    raw: bytes,
    sensor_types: Sequence[SensorTypeFacts],
    *,
    max_channels: int,
    max_bytes: int,
    criteria_available: bool,
) -> tuple[RegistryChannel, ...]:
    """Validate and map. Raises RegistryRejected with every finding; nothing is partly accepted."""
    if len(raw) > max_bytes:
        raise RegistryRejected(("document above the byte bound",))
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise RegistryRejected(("not JSON",)) from error
    channels = document.get("channels") if isinstance(document, dict) else None
    if not isinstance(channels, list) or set(document) != {"channels"}:
        raise RegistryRejected(("/: expected exactly a channels list",))
    if len(channels) > max_channels:
        raise RegistryRejected(("/channels: more than sensors.max_channels",))
    by_id = {s.id: s for s in sensor_types}
    version = registry_version(raw)
    findings: list[str] = []
    result: list[RegistryChannel] = []
    seen: set[str] = set()
    for index, entry in enumerate(channels):
        item, health = _item(index, entry)
        if item["channel_id"] in seen:
            findings.append(f"/channels/{index}/channel_id: duplicate")
        seen.add(item["channel_id"])
        problems = _cross_check(index, item, by_id.get(item["sensor_type_id"]), criteria_available=criteria_available)
        findings.extend(problems)
        if problems:
            continue
        result.append(
            RegistryChannel(
                ChannelFacts(
                    item["channel_id"],
                    item["sensor_type_id"],
                    item["hazard_id"],
                    item["adapter_id"],
                    item["origin_mode"],
                    item["location_label"],
                    item["source_label"],
                    item["dataset"],
                    item["display_note"],
                    item["expected_interval_seconds"],
                    item["stale_after_seconds"],
                    tuple(item["environment_ids"]),
                ),
                item["location"],
                bool(item["trusted"]),
                health,
                version,
            )
        )
    if findings:
        raise RegistryRejected(tuple(findings))
    return tuple(result)
