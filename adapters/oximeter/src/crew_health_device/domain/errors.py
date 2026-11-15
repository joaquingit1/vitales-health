"""Typed failures at device boundaries."""

from typing import Literal

FrameReason = Literal[
    "empty_frame",
    "frame_too_large",
    "truncated",
    "trailing_bytes",
    "reserved_flags",
    "reserved_value",
    "invalid_timestamp",
    "unknown_characteristic",
]


class FrameError(Exception):
    def __init__(self, reason: FrameReason, field: str | None = None) -> None:
        self.reason = reason
        self.field = field
        super().__init__(reason)


class ProfileError(Exception):
    def __init__(self, pointer: str, reason: str) -> None:
        self.pointer = pointer
        self.reason = reason
        super().__init__(f"{pointer}:{reason}")


class BindingError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
