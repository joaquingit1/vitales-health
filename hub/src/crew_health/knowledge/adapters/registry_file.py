from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from pydantic import TypeAdapter, ValidationError

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.adapters._inventory_models import Inventories
from crew_health.knowledge.adapters._manifest_models import InputsManifest
from crew_health.knowledge.adapters._registry_models import (
    ConditionInput,
    FamilyInput,
    MembershipInput,
    RegistryInput,
)
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash
from crew_health.knowledge.domain.registry import Condition, DeclaredSource, Membership, RegistrySnapshot


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceInvalid("duplicate_json_key")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise EvidenceInvalid("nonfinite_json")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceInvalid("nonfinite_json")
    return number


def _integer(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise EvidenceInvalid("json_integer") from error


def _unicode(value: object, maximum: int) -> None:
    pending = [value]
    for _ in range(maximum):
        if not pending:
            return
        item = pending.pop()
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    if pending:
        raise EvidenceInvalid("json_records")


def parse_json(raw: bytes, *, max_bytes: int, max_depth: int) -> object:
    require_bound(len(raw), max_bytes)
    require_bound(1, max_depth)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise EvidenceInvalid("json") from error
    depth, quoted, escaped = 0, False, False
    for value in raw:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            require_bound(depth, max_depth)
        elif value in (93, 125):
            depth -= 1
            if depth < 0:
                raise EvidenceInvalid("json_depth")
    try:
        value_object: object = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_finite_float,
            parse_int=_integer,
        )
        _unicode(value_object, len(raw))
        return value_object
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise EvidenceInvalid("json") from error


def byte_limit(settings: HubSettings, key: str) -> int:
    quantity = settings.quantity(key)
    if (
        quantity.unit.code != "By"
        or quantity.value != quantity.value.to_integral_value()
        or quantity.value < 1
    ):
        raise EvidenceInvalid("byte_setting")
    return int(quantity.value)


def load_manifest(raw: bytes, settings: HubSettings) -> InputsManifest:
    try:
        manifest = InputsManifest.model_validate(
            parse_json(
                raw,
                max_bytes=byte_limit(settings, "knowledge.bundle.max_manifest_bytes"),
                max_depth=settings.integer("knowledge.bundle.max_json_depth"),
            ),
            strict=True,
        )
    except ValidationError as error:
        raise EvidenceInvalid("input_manifest") from error
    require_bound(len(manifest.files), settings.integer("knowledge.bundle.max_members"))
    sources = {f.source for f in manifest.files}
    destinations = {f.destination for f in manifest.files}
    if not sources or len(sources) != len(manifest.files) or len(destinations) != len(manifest.files):
        raise EvidenceInvalid("input_manifest_members")
    for item in manifest.files:
        if (
            Path(item.source).name != Path(item.destination).name
            or Path(item.source).suffix[1:] != item.parser
        ):
            raise EvidenceInvalid("input_manifest_pair")
    return manifest


def pinned_file(root: Path, relative: str, expected_hash: str, maximum: int) -> bytes:
    require_hash(expected_hash)
    require_bound(1, maximum)
    root = root.resolve()
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise EvidenceInvalid("input_path")
    try:
        if candidate.resolve(strict=True) != candidate or not candidate.is_file():
            raise EvidenceInvalid("input_path")
        with candidate.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except FileNotFoundError as error:
        raise EvidenceInvalid("missing_input") from error
    except OSError as error:
        raise StorageUnavailable("io_error") from error
    require_bound(len(raw), maximum)
    if sha256(raw).hexdigest() != expected_hash:
        raise EvidenceInvalid("input_sha256")
    return raw


def input_copies(root: Path, manifest_raw: bytes, settings: HubSettings) -> dict[str, bytes]:
    manifest = load_manifest(manifest_raw, settings)
    result = {}
    total = 0
    for item in manifest.files:
        raw = pinned_file(
            root, item.destination, item.sha256, byte_limit(settings, "knowledge.bundle.max_member_bytes")
        )
        total += len(raw)
        require_bound(total, byte_limit(settings, "knowledge.bundle.max_total_expanded_bytes"))
        result[Path(item.destination).name] = raw
    return result


def _rows(raw: bytes, settings: HubSettings) -> list[dict[str, str]]:
    require_bound(len(raw), byte_limit(settings, "knowledge.bundle.max_member_bytes"))
    try:
        text = raw.decode("utf-8")
        rows = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        result: list[dict[str, str]] = []
        if rows.fieldnames is None or len(set(rows.fieldnames)) != len(rows.fieldnames):
            raise EvidenceInvalid("csv_header")
        for row in rows:
            require_bound(len(result) + 1, settings.integer("knowledge.registry.max_rows"))
            if None in row or any(value is None for value in row.values()):
                raise EvidenceInvalid("csv_row")
            result.append(row)
        return result
    except (csv.Error, UnicodeError) as error:
        raise EvidenceInvalid("csv") from error


def _sources(raw: bytes, nasa: bytes, settings: HubSettings) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for row in _rows(raw, settings):
        sources.append(
            dict(
                source_id=row["source_id"],
                title=row["title"],
                url=row["url"],
                declaring_input="sources.csv",
                date_text=None,
                access_text=row["access_status"] or None,
                saved_as=None,
            )
        )
    try:
        lines = nasa.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise EvidenceInvalid("source_text") from error
    for number, line in enumerate(lines, 1):
        require_bound(len(line.encode()), byte_limit(settings, "knowledge.bundle.max_line_bytes"))
        cells = line.split("|")
        if (
            len(cells) < 4
            or re.fullmatch(
                r"\s*(?:(?:EV|RT|RS|SK|S|C|N|L)\d{2})(?:,\s*(?:(?:EV|RT|RS|SK|S|C|N|L)\d{2}))*\s*", cells[1]
            )
            is None
        ):
            continue
        for title, url in re.findall(r"\[([^]]+)\]\((https?://[^\s)]+)\)", cells[2]):
            for identifier in cells[1].strip().split(", "):
                require_bound(len(sources) + 1, settings.integer("knowledge.registry.max_rows"))
                sources.append(
                    dict(
                        source_id=identifier,
                        title=title,
                        url=url,
                        declaring_input=f"NASA-SOURCES.md:{number}",
                        date_text=None,
                        access_text=None,
                        saved_as=None,
                    )
                )
    return sources


def build_registry_input(
    copies: dict[str, bytes],
    manifest_hash: str,
    inventories_raw: bytes,
    settings: HubSettings,
) -> RegistryInput:
    maximum = byte_limit(settings, "knowledge.bundle.max_member_bytes")
    depth = settings.integer("knowledge.bundle.max_json_depth")
    try:
        inventories = Inventories.model_validate(
            parse_json(inventories_raw, max_bytes=maximum, max_depth=depth), strict=True
        )
        conditions = TypeAdapter(list[ConditionInput]).validate_python(
            parse_json(copies["condition-assessments-119.json"], max_bytes=maximum, max_depth=depth),
            strict=True,
        )
        families = TypeAdapter(dict[str, FamilyInput]).validate_python(
            parse_json(copies["assessment-families.json"], max_bytes=maximum, max_depth=depth), strict=True
        )
        memberships = TypeAdapter(list[MembershipInput]).validate_python(
            _rows(copies["expanded-inventory-assessments-792.csv"], settings), strict=True
        )
        _check_inventories(conditions, memberships, inventories)
        _check_active_csv(conditions, copies["condition-assessments-119.csv"], settings)
        return RegistryInput.model_validate(
            dict(
                input_manifest_sha256=manifest_hash,
                conditions=conditions,
                memberships=memberships,
                sources=_sources(copies["sources.csv"], copies["NASA-SOURCES.md"], settings),
                families=families,
            ),
            strict=True,
        )
    except (KeyError, ValidationError) as error:
        raise EvidenceInvalid("registry_input") from error


def _check_active_csv(conditions: list[ConditionInput], raw: bytes, settings: HubSettings) -> None:
    csv_rows = _rows(raw, settings)
    if len(csv_rows) != len(conditions):
        raise EvidenceInvalid("condition_csv")
    for json_row, csv_row in zip(conditions, csv_rows, strict=True):
        if json_row.model_dump() != csv_row:
            raise EvidenceInvalid("condition_csv")


def _check_inventories(
    conditions: list[ConditionInput],
    memberships: list[MembershipInput],
    inventories: Inventories,
) -> None:
    definitions = {i.source_inventory: i for i in inventories.inventories}
    if len(definitions) != len(inventories.inventories):
        raise EvidenceInvalid("duplicate_inventory")
    counts = Counter(m.source_inventory for m in memberships)
    for name, definition in definitions.items():
        actual = len(conditions) if definition.status == "active" else counts.pop(name, 0)
        if actual != definition.expected_count:
            raise EvidenceInvalid("inventory_count")
    if counts or not any(i.status == "active" for i in definitions.values()):
        raise EvidenceInvalid("inventory_definition")
    for membership in memberships:
        prefix = definitions[membership.source_inventory].prefix
        if re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", membership.source_entry_id) is None:
            raise EvidenceInvalid("inventory_prefix")


def _fields(value: dict[str, object]) -> tuple[tuple[str, str | None], ...]:
    result = []
    for key, item in sorted(value.items()):
        if item is not None and not isinstance(item, str):
            raise EvidenceInvalid("record_field")
        result.append((key, item))
    return tuple(result)


def registry_snapshot(value: RegistryInput, planning_sentinel: str) -> RegistrySnapshot:
    conditions = tuple(
        Condition(
            c.condition_id,
            c.condition,
            "active",
            c.nasa_membership_source,
            c.family,
            _fields(c.model_dump()),
            tuple(s.strip() for s in c.workup_source_ids.split(";") if s.strip() != planning_sentinel),
            True,
        )
        for c in value.conditions
    )
    memberships = tuple(
        Membership(
            re.sub(r"[0-9]+$", "", m.source_entry_id),
            m.source_entry_id,
            m.condition,
            m.source_url,
            sha256(canonical_json(m.model_dump())).hexdigest(),
            m.published_mapping,
            tuple(re.findall(r"(?:ICL|MCL|PFC|ARC|PFCL|ARCL|RCL)[1-9][0-9]*[A-Z]*", m.published_mapping)),
            tuple(re.findall(r"ICL[1-9][0-9]*[A-Z]*", m.active_match)),
        )
        for m in value.memberships
    )
    active_memberships = tuple(
        Membership(
            "ICL",
            c.condition_id,
            c.condition,
            c.nasa_membership_source,
            sha256(canonical_json(c.model_dump())).hexdigest(),
            "",
            (),
            (),
        )
        for c in value.conditions
    )
    sources = tuple(
        DeclaredSource(s.source_id, s.title, s.url, s.declaring_input, s.date_text, s.access_text, s.saved_as)
        for s in value.sources
    )
    families = tuple(
        (key, tuple((k, str(v)) for k, v in sorted(f.model_dump().items())))
        for key, f in sorted(value.families.items())
    )
    return RegistrySnapshot(
        value.input_manifest_sha256, conditions, (*active_memberships, *memberships), sources, families
    )
