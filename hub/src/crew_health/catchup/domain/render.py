"""SYSTEM 5.6: unknown values retain the released unknown text."""

import re
from collections.abc import Mapping


def render(template: str, values: Mapping[str, str | None], unknown: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return unknown if value is None else value

    return re.sub(r"\{([a-z_]+)\}", replacement, template)
