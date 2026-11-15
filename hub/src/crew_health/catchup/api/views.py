"""CONTRACTS 26: exact GroundLogView projection and generated response validation."""

from datetime import datetime

from crew_health.catchup.application.ports import GroundLog
from crew_health.contracts_gen.schemas.ground_log_view_schema import GroundLogView


def instant(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat().replace("+00:00", "Z")


def log_wire(log: GroundLog) -> dict[str, object]:
    sections = ("what_happened", "what_was_done", "being_watched", "advice_welcome")
    wire: dict[str, object] = {
        "since": instant(log.since),
        "gap": None
        if log.gap is None
        else {
            "from": instant(log.gap.started_at),
            "to": instant(log.gap.ended_at),
            "gap_kind": log.gap.gap_kind,
            "gap_planned": log.gap.gap_planned,
        },
        "items": [
            {
                "kind": item.kind,
                "ref_id": item.ref_id,
                "title": item.title,
                "queued_at": instant(item.queued_at),
                "delivered_at": instant(item.delivered_at),
                "catch_up": {key: list(lines) for key, lines in zip(sections, item.catch_up, strict=True)},
                "plan_run_id": item.plan_run_id,
                "simulated": item.simulated,
            }
            for item in log.items
        ],
        "as_of": instant(log.as_of),
    }
    GroundLogView.model_validate(wire)
    return wire
