"""Startup-only validation of the policy and its provenance."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as TypedValidationError


class PolicyInvalid(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ModelPolicy(_Strict):
    id: str
    revision: str
    sha256: str | None


class IntervalPolicy(_Strict):
    low: str
    high: str


class ComparatorPolicy(_Strict):
    rolling_window_days: int


class ValueProvenance(_Strict):
    basis: Literal["sourced", "team default"]
    nasa_checked: list[str]
    package: list[str]
    to_be_set_by: str


class Provenance(_Strict):
    model: ValueProvenance
    min_context_days: ValueProvenance
    max_context_days: ValueProvenance
    horizon_days: ValueProvenance
    interval: ValueProvenance
    comparators: ValueProvenance
    max_series_points: ValueProvenance


class _Document(_Strict):
    version: str
    basis: str
    provenance: Provenance
    model: ModelPolicy
    cadence: Literal["mission_day"]
    min_context_days: int
    max_context_days: int
    horizon_days: int
    interval: IntervalPolicy
    comparators: ComparatorPolicy
    max_series_points: int


@dataclass(frozen=True)
class ForecastPolicy:
    version: str
    basis: str
    model: ModelPolicy
    cadence: str
    min_context_days: int
    max_context_days: int
    horizon_days: int
    low: Decimal
    high: Decimal
    rolling_window_days: int
    max_series_points: int


def load_policy(path: Path) -> ForecastPolicy:
    schema_path = Path(__file__).resolve().parents[3] / "config" / "forecast-policy.schema.json"
    try:
        with schema_path.open(encoding="utf-8") as stream:
            schema = json.load(stream)
        with path.open(encoding="utf-8") as stream:
            raw = json.load(stream)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(raw)
        document = _Document.model_validate(raw)
        low, high = Decimal(document.interval.low), Decimal(document.interval.high)
        if not Decimal(0) < low < Decimal("0.5") < high < Decimal(1):
            raise PolicyInvalid("invalid_interval")
        if document.min_context_days > document.max_context_days:
            raise PolicyInvalid("invalid_context_bounds")
        for entry in document.provenance.model_dump().values():
            if entry["basis"] == "sourced" and not entry["package"]:
                raise PolicyInvalid("sourced_value_without_source")
        return ForecastPolicy(
            document.version,
            document.basis,
            document.model,
            document.cadence,
            document.min_context_days,
            document.max_context_days,
            document.horizon_days,
            low,
            high,
            document.comparators.rolling_window_days,
            document.max_series_points,
        )
    except (OSError, ValueError, ValidationError, SchemaError, TypedValidationError) as exc:
        raise PolicyInvalid("policy_invalid") from exc
