"""Durable form of a rule input for the `evaluation_pending` row (E24 5.5): tagged JSON built from the dataclass
field types, parsed back by the same types. Only the closed set of input classes is decodable, never a class named by
the data."""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from crew_health.rules.domain import inputs as I  # noqa: E741
from crew_health.rules.domain.errors import InvariantViolation

_CLASSES: dict[str, type[Any]] = {
    c.__name__: c
    for c in (
        I.SensorEvaluatedInput, I.FreshnessInput, I.MissionEventInput, I.StateFactInput, I.AnswerInput,
        I.GroundLinkInput, I.SupplyCoverInput, I.InsightInput, I.InsightExpiredInput, I.ActivationInput,
        I.DeadlineInput,
    )
}  # fmt: skip
_MAX_DEPTH = 12


def _enc(value: object, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise InvariantViolation("input nesting exceeds its bound")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | frozenset):
        items = sorted(value, key=repr) if isinstance(value, frozenset) else value
        return [_enc(v, depth + 1) for v in items]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _enc(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)}
    raise InvariantViolation("input holds an unsupported value")


def dumps(value: I.RuleInput) -> tuple[str, str]:
    return type(value).__name__, json.dumps(_enc(value), sort_keys=True, separators=(",", ":"))


def _dec(tp: object, raw: object, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise InvariantViolation("input nesting exceeds its bound")
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if isinstance(tp, typing.TypeAliasType):
        return _dec(tp.__value__, raw, depth + 1)
    if origin in (typing.Union, types.UnionType):
        if raw is None:
            return None
        rest = [a for a in args if a is not type(None)]
        if len(rest) == 1:
            return _dec(rest[0], raw, depth + 1)
        for candidate in rest:  # multi-class unions never occur in inputs except bool | str | int | float
            if isinstance(raw, candidate):
                return raw
        raise InvariantViolation("input union not decodable")
    if origin is typing.Literal:
        return raw
    if origin is tuple:
        if not isinstance(raw, list):
            raise InvariantViolation("input list expected")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_dec(args[0], v, depth + 1) for v in raw)
        return tuple(_dec(a, v, depth + 1) for a, v in zip(args, raw, strict=True))
    if origin is frozenset:
        if not isinstance(raw, list):
            raise InvariantViolation("input list expected")
        return frozenset(_dec(args[0], v, depth + 1) for v in raw)
    return _dec_plain(tp, raw, depth)


def _dec_plain(tp: object, raw: object, depth: int) -> object:
    if tp is datetime:
        return datetime.fromisoformat(str(raw))
    if tp is Decimal:
        return Decimal(str(raw))
    if tp in (str, int, bool, float, object):
        return raw
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(raw)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if not isinstance(raw, dict):
            raise InvariantViolation("input object expected")
        hints = typing.get_type_hints(tp)
        return tp(**{f.name: _dec(hints[f.name], raw[f.name], depth + 1) for f in dataclasses.fields(tp)})
    raise InvariantViolation("input type not decodable")


def loads(kind: str, text: str) -> I.RuleInput:
    cls = _CLASSES.get(kind)
    if cls is None:
        raise InvariantViolation("unknown pending input kind")
    value = _dec(cls, json.loads(text))
    return typing.cast(I.RuleInput, value)
