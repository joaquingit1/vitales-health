from __future__ import annotations


class BenchError(Exception):
    """A reference-only evaluation failure safe to expose through the CLI."""

    def __init__(self, reason: str, *, instance: str = "", exit_code: int = 4) -> None:
        self.reason = reason
        self.instance = instance
        self.exit_code = exit_code
        super().__init__(reason)
