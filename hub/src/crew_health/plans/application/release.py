"""Snapshot-pinned configuration reads and bounded parsed plan cache."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from decimal import Decimal
from threading import Lock
from typing import cast

from crew_health.kernel.config import ConfigDocumentView, ConfigName, ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.availability import StockLevel
from crew_health.plans.domain.model import (
    Plan,
    PlansDocument,
    SupplyInfo,
    all_steps,
    parse_document,
    parse_plan,
)

# E32's mechanical mapping: unit words are UCUM annotations unless already recognized UCUM codes.
_UCUM_CODES = ("1", "mL", "L", "g", "mg", "kg", "m", "cm")
_PIN_NAMES = (
    ConfigName.ONBOARD_PLANS,
    ConfigName.SKILLS_CATALOG,
    ConfigName.ROLE_RULES,
    ConfigName.KIT_MAP,
    ConfigName.UI_COPY,
)


def document_view(release: ReleaseHandle, name: ConfigName) -> ConfigDocumentView | None:
    try:
        release.version_of(name)
        return release.document(name)
    except StorageUnavailable:
        raise
    except ProblemError as error:
        # MemoryRelease represents an absent document as CONFIG_INVALID.
        if error.code != ErrorCode.CONFIG_INVALID:
            raise
        return None
    except KeyError:
        # Mapping-backed handles may expose a missing key directly.
        return None


def read_document(release: ReleaseHandle, name: ConfigName) -> Mapping[str, object] | None:
    view = document_view(release, name)
    if view is None:
        return None
    return object_value(json.loads(view.raw))


def object_value(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("expected configuration object")
    return cast(Mapping[str, object], value)


def array_value(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected configuration array")
    return cast(list[object], value)


def string_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected configuration string")
    return value


def kit_supplies(release: ReleaseHandle) -> Mapping[str, SupplyInfo]:
    doc = read_document(release, ConfigName.KIT_MAP)
    if doc is None:
        return {}
    result: dict[str, SupplyInfo] = {}
    for value in array_value(doc.get("supplies", [])):
        row = object_value(value)
        supply_id = string_value(row["supply_id"])
        word = string_value(row["unit"])
        unit = Unit(word if word in _UCUM_CODES else "{" + word + "}")
        consumable = row["consumable"]
        if not isinstance(consumable, bool):
            raise ValueError("expected consumable boolean")
        result[supply_id] = SupplyInfo(supply_id, unit, consumable)
    return result


def kit_for(release: ReleaseHandle, environment_id: str) -> frozenset[str] | None:
    doc = read_document(release, ConfigName.KIT_MAP)
    if doc is None:
        return None
    kit = object_value(doc["kits"]).get(environment_id)
    return None if kit is None else frozenset(string_value(v) for v in array_value(kit))


def packed_stock(release: ReleaseHandle) -> Mapping[str, Mapping[str, StockLevel]]:
    doc = read_document(release, ConfigName.KIT_MAP)
    if doc is None:
        return {}
    supplies = kit_supplies(release)
    result: dict[str, Mapping[str, StockLevel]] = {}
    for environment, values in object_value(doc.get("stock", {})).items():
        stock: dict[str, StockLevel] = {}
        for value in array_value(values):
            row = object_value(value)
            supply_id = string_value(row["supply_id"])
            raw = row["quantity"]
            quantity = None
            if raw is not None:
                if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
                    raise ValueError("expected stock quantity")
                quantity = Quantity(Decimal(str(raw)), supplies[supply_id].unit)
            stock[supply_id] = StockLevel(supply_id, True, quantity)
        result[environment] = stock
    return result


def return_option_present(release: ReleaseHandle, environment_id: str) -> bool:
    doc = read_document(release, ConfigName.ENVIRONMENT_PROFILES)
    if doc is None:
        return False
    return any(
        (row := object_value(value)).get("environment_id") == environment_id
        and row.get("return_option") is not None
        for value in array_value(doc["profiles"])
    )


def month_days(release: ReleaseHandle) -> int | None:
    doc = read_document(release, ConfigName.ROUTINE_RULES)
    if doc is None:
        return None
    value = object_value(doc.get("calendar", {})).get("month_days")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("invalid month_days")
    return value


def pins(release: ReleaseHandle) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name.value, view.version)
        for name in _PIN_NAMES
        if (view := document_view(release, name)) is not None
    )


class UiCopy:
    def __init__(self, document: Mapping[str, object]) -> None:
        self._document = document

    @classmethod
    def from_release(cls, release: ReleaseHandle) -> UiCopy | None:
        document = read_document(release, ConfigName.UI_COPY)
        return None if document is None else cls(document)

    def text(self, section: str, key: str) -> str | None:
        values = self._document.get(section)
        if not isinstance(values, dict):
            return None
        value = values.get(key)
        return value if isinstance(value, str) else None

    @staticmethod
    def render(template: str, values: Mapping[str, str]) -> str:
        for key in sorted(values):
            template = template.replace("{" + key + "}", values[key])
        return template


class PlansCatalog:
    def __init__(self, settings: PlansSettings) -> None:
        self._settings = settings
        self._cache: OrderedDict[str, PlansDocument] = OrderedDict()
        self._raw: OrderedDict[str, Mapping[str, object]] = OrderedDict()
        self._supply_versions: dict[str, str | None] = {}
        self._pinned: OrderedDict[tuple[str, str, str, tuple[tuple[str, str, bool], ...]], Plan] = (
            OrderedDict()
        )
        self._lock = Lock()

    def document(self, release: ReleaseHandle) -> PlansDocument | None:
        view = document_view(release, ConfigName.ONBOARD_PLANS)
        if view is None:
            return None
        if len(view.raw) > self._settings.max_document_bytes:
            raise ValueError("plans document exceeds byte bound")
        digest = hashlib.sha256(view.raw).hexdigest()
        kit_view = document_view(release, ConfigName.KIT_MAP)
        supply_version = None if kit_view is None else kit_view.version
        with self._lock:
            if digest in self._cache and self._supply_versions[digest] == supply_version:
                self._cache.move_to_end(digest)
                self._raw.move_to_end(digest)
                return self._cache[digest]
            raw = object_value(json.loads(view.raw))
            doc = parse_document(raw, document_sha256=digest, supplies=kit_supplies(release))
            self._validate_bounds(doc)
            self._cache[digest] = doc
            self._cache.move_to_end(digest)
            self._raw[digest] = raw
            self._raw.move_to_end(digest)
            self._supply_versions[digest] = supply_version
            while len(self._cache) > self._settings.parsed_plan_cache_entries:
                oldest, _ = self._cache.popitem(last=False)
                self._raw.pop(oldest)
                self._supply_versions.pop(oldest)
            return doc

    def parse_pinned(self, version: str, plan_id: str, raw: str, supplies: Mapping[str, SupplyInfo]) -> Plan:
        data = raw.encode("utf-8")
        if len(data) > self._settings.max_document_bytes:
            raise ValueError("pinned plan exceeds byte bound")
        metadata = tuple(sorted((key, info.unit.code, info.consumable) for key, info in supplies.items()))
        key = (version, plan_id, hashlib.sha256(data).hexdigest(), metadata)
        with self._lock:
            if key in self._pinned:
                self._pinned.move_to_end(key)
                return self._pinned[key]
            plan = parse_plan(object_value(json.loads(raw)), supplies=supplies)
            if plan.plan_id != plan_id:
                raise ValueError("pinned plan identity mismatch")
            if len(all_steps(plan)) > self._settings.max_steps_per_plan:
                raise ValueError("pinned plan exceeds step bound")
            self._pinned[key] = plan
            while len(self._pinned) > self._settings.parsed_plan_cache_entries:
                self._pinned.popitem(last=False)
            return plan

    def _validate_bounds(self, doc: PlansDocument) -> None:
        if len(doc.plans) + len(doc.rejected_plan_ids) > self._settings.max_plans_per_document:
            raise ValueError("plans document exceeds plan bound")
        if any(len(all_steps(plan)) > self._settings.max_steps_per_plan for plan in doc.plans):
            raise ValueError("plan exceeds step bound")

    def raw_plan(self, release: ReleaseHandle, plan_id: str) -> Mapping[str, object] | None:
        doc = self.document(release)
        if doc is None:
            return None
        with self._lock:
            raw = self._raw.get(doc.document_sha256)
        if raw is None:
            # Another thread can evict this entry between the two bounded cache reads.
            return self._raw_plan_uncached(release, plan_id)
        return self._find_raw(raw, plan_id)

    def _raw_plan_uncached(self, release: ReleaseHandle, plan_id: str) -> Mapping[str, object] | None:
        raw = read_document(release, ConfigName.ONBOARD_PLANS)
        return None if raw is None else self._find_raw(raw, plan_id)

    @staticmethod
    def _find_raw(raw: Mapping[str, object], plan_id: str) -> Mapping[str, object] | None:
        return next(
            (
                row
                for value in array_value(raw["plans"])
                if (row := object_value(value)).get("plan_id") == plan_id
            ),
            None,
        )
