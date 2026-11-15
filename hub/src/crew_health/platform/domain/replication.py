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
