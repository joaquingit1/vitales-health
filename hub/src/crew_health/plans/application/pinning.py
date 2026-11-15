"""Immutable configuration recovery data for a run's original semantics."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock

from crew_health.kernel.config import ConfigName, ReleaseHandle
from crew_health.kernel.units import Unit
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.plans.application.release import (
    UiCopy,
    kit_supplies,
    month_days,
    object_value,
    read_document,
)
from crew_health.plans.application.repository import PlanRunsRepository
from crew_health.plans.domain.model import Plan, SupplyInfo, all_steps, canonical_json


@dataclass(frozen=True, slots=True)
class PinnedContext:
    plan_json: str
    supplies: Mapping[str, SupplyInfo]
    copy: UiCopy | None
    month_days: int | None
    kits: Mapping[str, frozenset[str]] | None


def opening_context(release: ReleaseHandle, plan: Plan, raw: Mapping[str, object]) -> str:
    supplies = kit_supplies(release)
    kit_map = read_document(release, ConfigName.KIT_MAP)
    used = sorted({n.supply_id for s in all_steps(plan) for n in s.needs.supplies})
    return canonical_json(
        {
            "plan": raw,
            "supplies": [
                {"supply_id": key, "unit": supplies[key].unit.code, "consumable": supplies[key].consumable}
                for key in used
                if key in supplies
            ],
            "ui_copy": read_document(release, ConfigName.UI_COPY),
            "month_days": month_days(release),
            "kits": None if kit_map is None else kit_map.get("kits"),
        }
    )


def load_context(
    repository: PlanRunsRepository, view: ReadSnapshot | UnitOfWork, run_id: str, max_bytes: int
) -> PinnedContext | None:
    row = repository.pinned_context(view, run_id)
    if row is None:
        return None
    return decode_context(row, max_bytes)


def decode_context(row: tuple[str, str], max_bytes: int) -> PinnedContext | None:
    raw, digest = row
    if len(raw.encode("utf-8")) > max_bytes or hashlib.sha256(raw.encode("utf-8")).hexdigest() != digest:
        return None
    try:
        value = object_value(json.loads(raw))
        plan = object_value(value["plan"])
        supply_rows = value["supplies"]
        if not isinstance(supply_rows, list):
            return None
        supplies: dict[str, SupplyInfo] = {}
        for item in supply_rows:
            supply = object_value(item)
            sid, unit, consumable = supply["supply_id"], supply["unit"], supply["consumable"]
            if not isinstance(sid, str) or not isinstance(unit, str) or not isinstance(consumable, bool):
                return None
            supplies[sid] = SupplyInfo(sid, Unit(unit), consumable)
        copy_raw, days = value["ui_copy"], value["month_days"]
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days < 1):
            return None
        return PinnedContext(
            canonical_json(plan),
            supplies,
            None if copy_raw is None else UiCopy(object_value(copy_raw)),
            days,
            _kits(value.get("kits")),
        )
    except ValueError, KeyError, TypeError:
        return None  # Content failure only. The repository call remains outside this guard.


def _kits(raw: object) -> Mapping[str, frozenset[str]] | None:
    """The kit lists pinned at opening; a context written without them reads as not pinned."""
    if raw is None:
        return None
    kits: dict[str, frozenset[str]] = {}
    for environment, items in object_value(raw).items():
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError("expected kit list")
        kits[environment] = frozenset(str(item) for item in items)
    return kits


class PinnedContexts:
    """A per-world bounded LRU; only verified content hashes enter the cache."""

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.cache: OrderedDict[str, PinnedContext] = OrderedDict()
        self.lock = Lock()

    def load(
        self, repository: PlanRunsRepository, view: ReadSnapshot | UnitOfWork, run_id: str
    ) -> PinnedContext | None:
        row = repository.pinned_context(view, run_id)
        if row is None:
            return None
        raw, digest = row
        if (
            len(raw.encode("utf-8")) > self.max_bytes
            or hashlib.sha256(raw.encode("utf-8")).hexdigest() != digest
        ):
            return None
        with self.lock:
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest]
            parsed = decode_context(row, self.max_bytes)
            if parsed is not None:
                self.cache[digest] = parsed
                while len(self.cache) > self.max_entries:
                    self.cache.popitem(last=False)
            return parsed
