from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crew_health.kernel.replication import RevocationControl
from collections.abc import Mapping

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.replication import Delivery, ProjectionBody, ProjectionKey
from crew_health.platform.domain.errors import fail


def delivery_bytes(delivery: Delivery) -> bytes:
    body = delivery.body
    return canonical_json(
        {
            "stream_id": delivery.stream_id,
            "policy_epoch": delivery.policy_epoch,
            "delivery_seq": delivery.delivery_seq,
            "recipient_id": delivery.recipient_id,
            "purpose": delivery.purpose,
            "source_node_id": delivery.source_node_id,
            "source_incarnation": delivery.source_incarnation,
            "origin_token": delivery.origin_token,
            "body": {
                "key": {"owner": body.key.owner, "name": body.key.name, "version": body.key.version},
                "object_ref": body.object_ref,
                "selected_fields": list(body.selected_fields),
                "data_json": body.data_json.decode(),
                "dependencies": [
                    {
                        "field_pointer": d.field_pointer,
                        "record_ref": d.record_ref,
                        "disposition": d.disposition,
                    }
                    for d in body.dependencies
                ],
            },
        }
    )


def delivery_hash(delivery: Delivery) -> str:
    return hashlib.sha256(b"crew-health.delivery.v1\0" + delivery_bytes(delivery)).hexdigest()


def leaf_pointers(value: object, *, maximum: int) -> frozenset[str]:
    stack: list[tuple[str, object]] = [("", value)]
    result: set[str] = set()
    for _ in range(maximum * 4 + 1):
        if not stack:
            return frozenset(result)
        path, item = stack.pop()
        if isinstance(item, dict) and item:
            for key, child in item.items():
                if not isinstance(key, str):
                    raise fail(ErrorCode.INVALID_VALUE)
                stack.append((path + "/" + key.replace("~", "~0").replace("/", "~1"), child))
        elif isinstance(item, list) and item:
            for index, child in enumerate(item):
                stack.append((path + "/" + str(index), child))
        else:
            result.add(path)
        if len(stack) + len(result) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
    raise fail(ErrorCode.PAYLOAD_TOO_LARGE)


def catalog_key(key: ProjectionKey) -> bool:
    catalog = {
        "help": ("broadcast", "task", "approved"),
        "mission": ("context",),
        "sensors": ("reading",),
        "rules": ("operational",),
        "routine": ("operational", "ownership", "action"),
        "records": ("observation", "answer"),
        "checks": ("daily-check",),
        "episodes": ("episode",),
        "plans": ("plan-run",),
        "teach": ("guided-check",),
        "roles": ("task",),
        "supplies": ("ownership",),
        "handoff": ("approved",),
        "catchup": ("approved",),
        "config": ("release",),
        "knowledge": ("release",),
        "platform": ("revocation", "receipt"),
        "participation": ("goal",),
    }
    return key.version == 1 and key.name in catalog.get(key.owner, ())


def validate_body(body: ProjectionBody, *, allowed_fields: frozenset[str], maximum: int) -> None:
    if (
        tuple(sorted(set(body.selected_fields))) != body.selected_fields
        or not set(body.selected_fields) <= allowed_fields
    ):
        raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
    try:
        value: object = json.loads(body.data_json)
        if canonical_json(value) != body.data_json:
            raise fail(ErrorCode.INVALID_VALUE)
    except (ValueError, UnicodeError) as error:
        raise fail(ErrorCode.INVALID_VALUE) from error
    if leaf_pointers(value, maximum=maximum) != frozenset(body.selected_fields):
        raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
    for dependency in body.dependencies:
        if dependency.field_pointer not in allowed_fields:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if dependency.disposition == "omitted" and dependency.record_ref is not None:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if dependency.disposition == "required" and dependency.record_ref is None:
            raise fail(ErrorCode.INVALID_VALUE)


def schema_leaf_fields(schema: Mapping[str, object], *, maximum: int) -> frozenset[str]:
    stack: list[tuple[str, Mapping[str, object]]] = [("", schema)]
    result: set[str] = set()
    for _ in range(maximum * 4 + 1):
        if not stack:
            return frozenset(result)
        path, item = stack.pop()
        properties = item.get("properties")
        if isinstance(properties, dict):
            if item.get("additionalProperties") is not False:
                raise fail(ErrorCode.CONFIG_INVALID)
            for key, child in properties.items():
                if not isinstance(key, str) or not isinstance(child, dict):
                    raise fail(ErrorCode.CONFIG_INVALID)
                stack.append((path + "/" + key.replace("~", "~0").replace("/", "~1"), child))
        else:
            if "$ref" in item or item.get("type") == "array":
                raise fail(ErrorCode.CONFIG_INVALID)
            result.add(path)
        if len(result) + len(stack) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
    raise fail(ErrorCode.PAYLOAD_TOO_LARGE)


def control_bytes(control: RevocationControl) -> bytes:
    return canonical_json(
        {
            "control_id": control.control_id,
            "recipient_id": control.recipient_id,
            "stream_id": control.stream_id,
            "old_policy_epoch": control.old_policy_epoch,
            "new_policy_epoch": control.new_policy_epoch,
            "authority_ref": control.authority_ref,
            "signature": control.signature.hex(),
        }
    )


def parse_control(raw: bytes) -> RevocationControl:
    from crew_health.kernel.replication import RevocationControl

    try:
        value: object = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "control_id",
            "recipient_id",
            "stream_id",
            "old_policy_epoch",
            "new_policy_epoch",
            "authority_ref",
            "signature",
        }:
            raise ValueError("control schema")
        text: dict[str, str] = {}
        for key in ("control_id", "recipient_id", "stream_id", "authority_ref", "signature"):
            field: object = value[key]
            if not isinstance(field, str):
                raise ValueError("control text")
            text[key] = field
        old: object = value["old_policy_epoch"]
        new: object = value["new_policy_epoch"]
        if type(old) is not int or type(new) is not int or not 0 < old < new:
            raise ValueError("control epoch")
        return RevocationControl(
            text["control_id"],
            text["recipient_id"],
            text["stream_id"],
            old,
            new,
            text["authority_ref"],
            bytes.fromhex(text["signature"]),
        )
    except ValueError, TypeError, KeyError:
        raise fail(ErrorCode.INVALID_VALUE) from None


def projection_hash(body: ProjectionBody) -> str:
    """Hash the entire projection, independently of its delivery stream and sequence."""
    envelope = Delivery("", 0, 0, "", "", "", "", "", body, "")
    return hashlib.sha256(b"crew-health.projection.v1\0" + delivery_bytes(envelope)).hexdigest()


_ANNOTATIONS = frozenset({"$defs", "$schema", "$id", "title", "description", "default"})
type _Frame = tuple[dict[str, object], Mapping[str, object], bool]


def _definition(schema: Mapping[str, object], reference: object) -> Mapping[str, object]:
    definitions = schema.get("$defs")
    if (
        not isinstance(reference, str)
        or not reference.startswith("#/$defs/")
        or not isinstance(definitions, dict)
    ):
        raise fail(ErrorCode.CONFIG_INVALID)
    resolved = definitions.get(reference.removeprefix("#/$defs/"))
    if not isinstance(resolved, dict):
        raise fail(ErrorCode.CONFIG_INVALID)
    return resolved


def _copy_list(value: list[object], stack: list[_Frame]) -> list[object]:
    children: list[object] = []
    for entry in value:
        if isinstance(entry, dict):
            child: dict[str, object] = {}
            children.append(child)
            stack.append((child, entry, False))
        else:
            children.append(entry)
    return children


def _expand(schema: Mapping[str, object], frame: _Frame, stack: list[_Frame]) -> None:
    target, item, property_names = frame
    if not property_names and "$ref" in item:
        stack.append((target, _definition(schema, item["$ref"]), False))
    for key, value in item.items():
        if not property_names and (key in _ANNOTATIONS or key == "$ref"):
            continue
        if isinstance(value, dict):
            child: dict[str, object] = {}
            target[key] = child
            stack.append((child, value, not property_names and key == "properties"))
        elif isinstance(value, list):
            target[key] = _copy_list(value, stack)
        else:
            target[key] = value


def schema_constraints(schema: Mapping[str, object], *, maximum: int) -> dict[str, object]:
    """Resolve local generated-schema references with a bounded, iterative traversal."""
    result: dict[str, object] = {}
    stack: list[_Frame] = [(result, schema, False)]
    for _ in range(maximum * 32):
        if not stack:
            return result
        _expand(schema, stack.pop(), stack)
        if len(stack) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
    raise fail(ErrorCode.CONFIG_INVALID)


def _object_variants(variants: object) -> list[Mapping[str, object]]:
    if not isinstance(variants, list):
        return []
    return [child for child in variants if isinstance(child, dict) and "properties" in child]


def _is_leaf(item: Mapping[str, object]) -> bool:
    variants = item.get("anyOf")
    composite = isinstance(variants, list) and any(
        isinstance(child, dict) and child.get("type") in ("array", "object") for child in variants
    )
    return item.get("type") not in ("array", "object") and not composite


def _children(pointer: str, item: Mapping[str, object]) -> list[tuple[str, Mapping[str, object]]] | None:
    """The nodes below one schema node, or None when the node is itself a leaf candidate."""
    properties = item.get("properties")
    if isinstance(properties, dict):
        return [
            (pointer + "/" + key.replace("~", "~0").replace("/", "~1"), child)
            for key, child in properties.items()
            if isinstance(key, str) and isinstance(child, dict)
        ]
    variants = _object_variants(item.get("anyOf"))
    return [(pointer, child) for child in variants] if variants else None


def effective_leaf_schemas(schema: Mapping[str, object], *, maximum: int) -> dict[str, Mapping[str, object]]:
    """Open maps and arrays have no resolved leaf projection binding and stay unavailable."""
    if not schema:
        return {}
    stack: list[tuple[str, Mapping[str, object]]] = [("", schema_constraints(schema, maximum=maximum))]
    leaves: dict[str, Mapping[str, object]] = {}
    for _ in range(maximum * 4 + 1):
        if not stack:
            return leaves
        pointer, item = stack.pop()
        children = _children(pointer, item)
        if children is not None:
            stack.extend(children)
        elif _is_leaf(item):
            leaves[pointer] = item
        if len(stack) + len(leaves) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
    raise fail(ErrorCode.PAYLOAD_TOO_LARGE)


def catalog_leaf_schemas(
    key: ProjectionKey, schema: Mapping[str, object], *, maximum: int
) -> dict[str, Mapping[str, object]]:
    leaves = effective_leaf_schemas(schema, maximum=maximum)
    forbidden = {
        "journal",
        "prompt",
        "private_attachment",
        "private_communication",
        "report_source",
        "report_draft",
    }
    if key.name == "operational" or key.owner in {"mission", "sensors"}:
        forbidden |= {
            "patient_id",
            "person_id",
            "operator_id",
            "subject",
            "person",
            "actor",
            "notes",
            "note",
            "skip",
            "outcome",
            "by",
            "display_name",
            "resolved",
            "dismissed",
        }
    approved = {
        "handoff": {"handoff_id", "revision", "fields", "catch_up", "approved_at"},
        "help": {
            "ground_message_id",
            "session_revision",
            "lines",
            "text",
            "text_sha256",
            "catch_up",
            "queued_at",
        },
        "catchup": {
            "ground_message_id",
            "session_revision",
            "lines",
            "text",
            "text_sha256",
            "catch_up",
            "queued_at",
        },
    }
    roots = approved.get(key.owner) if key.name == "approved" else None
    return {
        pointer: value
        for pointer, value in leaves.items()
        if not forbidden.intersection(pointer.split("/"))
        and (roots is None or pointer.split("/")[1] in roots)
        and not (key.owner == "participation" and pointer in {"/completed_total", "/due_total"})
    }
