"""Scenario definitions are data, while protocol fault behavior stays explicit."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from .physiology import decimal_map, object_map, read_document


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    parameters: Mapping[str, Decimal]


def load_scenarios(path: Path | None = None) -> dict[str, Scenario]:
    document = object_map(read_document("scenarios.json", path)["scenarios"])
    result: dict[str, Scenario] = {}
    for name, raw in document.items():
        item = object_map(raw)
        description = item["description"]
        if not isinstance(description, str) or not description:
            raise ValueError("scenario description required")
        parameters = decimal_map(item["parameters"])
        for key in ("samples", "notifications", "notification_index"):
            if key in parameters and (parameters[key] < 1 or parameters[key] != int(parameters[key])):
                raise ValueError("scenario count must be a positive integer")
        if "from_notification" in parameters:
            index = parameters["from_notification"]
            if index < 0 or index != int(index):
                raise ValueError("freeze index must be a nonnegative integer")
        if "percent" in parameters and not 0 <= parameters["percent"] <= 100:
            raise ValueError("battery percent outside range")
        if parameters.get("noise_multiplier", Decimal(1)) < 0:
            raise ValueError("negative noise multiplier")
        result[name] = Scenario(name, description, MappingProxyType(parameters))
    return result
