from __future__ import annotations

from collections.abc import Mapping

from crew_health_bench.domain.cases import ConfigPrecondition
from crew_health_bench.domain.documents import json_pointer


def check_preconditions(requires: tuple[ConfigPrecondition, ...], documents: Mapping[str, object]) -> bool:
    for requirement in requires:
        value = json_pointer(documents, requirement.document, requirement.pointer)
        if (value is None) != (requirement.must_be == "null"):
            return False
    return True


def require_independent_labels(source: str, source_ids: tuple[str, ...], urgency: str) -> None:
    from crew_health_bench.domain.errors import BenchError

    if not source_ids or source.casefold().startswith(("config:", "configuration:")):
        raise BenchError("case_invalid")
    if "COORDINATOR-DECISIONS D2" in source and urgency != "emergency":
        raise BenchError("case_invalid")
