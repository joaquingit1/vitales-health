"""Template rendering for help texts (CONTRACTS 11 "Templates").

A placeholder with no value renders ui-copy designed_states.unknown: missing stays visibly missing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def render(template: str, values: Mapping[str, str | None], unknown: str) -> str:
    def substitute(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return value if value else unknown

    return _PLACEHOLDER.sub(substitute, template)


def join_names(names: tuple[str, ...], separator: str) -> str | None:
    return separator.join(names) if names else None
