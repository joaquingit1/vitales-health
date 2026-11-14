from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from crew_health.config.domain.errors import ConfigInvalid
from crew_health.kernel.config import CandidateRelease, ConfigName, Finding


def parse_document(raw: bytes) -> object:
    try:
        parsed = cast(
            object,
            json.loads(raw, parse_constant=_nonfinite, parse_float=_finite_float, object_pairs_hook=_unique),
        )
        for pointer, value in walk(parsed):
            if pointer.count("/") > 64:
                raise ConfigInvalid("document nesting bound exceeded")
            if isinstance(value, str):
                value.encode("utf-8")
        return parsed
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ConfigInvalid("invalid JSON document") from exc


def _nonfinite(value: str) -> object:
    raise ValueError("nonfinite JSON number")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def walk(value: object, *, maximum: int = 100000) -> Iterator[tuple[str, object]]:
    pending = [("", value)]
    for _ in range(maximum):
        if not pending:
            return
        pointer, item = pending.pop()
        yield pointer, item
        if isinstance(item, dict):
            members = cast(dict[str, object], item)
            pending.extend(
                (pointer + "/" + key.replace("~", "~0").replace("/", "~1"), members[key])
                for key in sorted(members, reverse=True)
            )
        elif isinstance(item, list):
            items = cast(list[object], item)
            pending.extend(
                (pointer + "/" + str(index), items[index]) for index in reversed(range(len(items)))
            )
        if len(pending) > maximum:
            raise ConfigInvalid("document traversal bound exceeded")
    if pending:
        raise ConfigInvalid("document traversal bound exceeded")


@dataclass(frozen=True, slots=True)
class SourceCheck:
    source_ids: frozenset[str]
    check_id: str = "2"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        findings = []
        for name, document in sorted(candidate.documents.items()):
            for pointer, value in walk(parse_document(document.raw)):
                if pointer.endswith("source_id") and isinstance(value, str) and value not in self.source_ids:
                    findings.append(Finding(self.check_id, name, pointer, "source does not resolve"))
                if isinstance(value, str):
                    unresolved = {
                        source
                        for source in re.findall(r"\b(?:EV|RT|RS|SK|S|C|N|L)\d{2}\b", value)
                        if source not in self.source_ids
                    }
                    if unresolved:
                        findings.append(
                            Finding(self.check_id, name, pointer or "/", "source does not resolve")
                        )
                if pointer.endswith("source_ids") and isinstance(value, list):
                    for index, source in enumerate(cast(list[object], value)):
                        if not isinstance(source, str) or source not in self.source_ids:
                            findings.append(
                                Finding(
                                    self.check_id, name, pointer + "/" + str(index), "source does not resolve"
                                )
                            )
        return tuple(findings)


@dataclass(frozen=True, slots=True)
class IndicatorCheck:
    field_ids: frozenset[str]
    check_id: str = "9"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        document = candidate.documents.get(ConfigName.INDICATOR_CATALOG)
        if document is None:
            return (Finding(self.check_id, ConfigName.INDICATOR_CATALOG, "/", "indicator catalog missing"),)
        return tuple(
            Finding(self.check_id, document.name, pointer, "code is absent from input dictionary")
            for pointer, value in walk(parse_document(document.raw))
            if pointer.endswith("/code") and (not isinstance(value, str) or value not in self.field_ids)
        )


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number
