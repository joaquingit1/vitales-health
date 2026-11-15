"""Readings on the wire: the AdapterReadingBatch body of ingestReadings.

Schema: contracts/api/schemas/requests.schema.json.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from crew_health_sim_habitat.errors import SimulatorError
from crew_health_sim_habitat.units import Quantity

type Json = dict[str, object]


@dataclass(frozen=True, slots=True)
class WireReading:
    channel_id: str
    quantity: Quantity
    observed_at: datetime
    origin_mode: str
    quality_status: str
    aggregation: str | None = None
    window_seconds: int | None = None
    replay_dataset: str | None = None
    replay_time_basis: str | None = None
    cabin_pressure_mmhg: float | None = None
    cabin_pressure_basis: str | None = None


def format_instant(moment: datetime) -> str:
    """RFC 3339 UTC with a Z suffix (common.schema.json $defs/instant)."""
    if moment.tzinfo is None:
        raise SimulatorError("instants must be timezone aware")
    utc = moment.astimezone(UTC)
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        text += f".{utc.microsecond:06d}".rstrip("0")
    return text + "Z"


def reading_json(reading: WireReading, sequence: int) -> Json:
    body: Json = {
        "channel_id": reading.channel_id,
        "adapter_sequence": sequence,
        "value": reading.quantity.value,
        "unit": reading.quantity.unit,
        "observed_at": format_instant(reading.observed_at),
        "origin_mode": reading.origin_mode,
        "quality_status": reading.quality_status,
    }
    if reading.aggregation is not None:
        body["aggregation"] = {"kind": reading.aggregation, "window_seconds": reading.window_seconds}
    if reading.replay_dataset is not None and reading.replay_time_basis is not None:
        body["replay"] = {"dataset": reading.replay_dataset, "time_basis": reading.replay_time_basis}
    if reading.cabin_pressure_mmhg is not None and reading.cabin_pressure_basis is not None:
        body["cabin_pressure_mmhg"] = {
            "value": reading.cabin_pressure_mmhg,
            "basis": reading.cabin_pressure_basis,
        }
    return body


def batch_json(batch_id: str, items: Sequence[tuple[int, WireReading]]) -> Json:
    return {"batch_id": batch_id, "readings": [reading_json(r, seq) for seq, r in items]}
