"""CONTRACTS 12: the single GroundLink projection."""

from datetime import datetime

from crew_health.groundlink.application.ports import GroundLinkView


def instant(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def link_wire(view: GroundLinkView) -> dict[str, object]:
    return {
        "status": view.status,
        "one_way_delay_s": view.one_way_delay_s,
        "next_contact_at": instant(view.next_contact_at),
        "last_contact_at": instant(view.last_contact_at),
        "source": view.source,
        "environment_id": view.environment_id,
        "delay_text": view.delay_text,
        "delay_source_ids": list(view.delay_source_ids),
        "gap_kind": view.gap_kind,
        "gap_planned": view.gap_planned,
    }
