"""SYSTEM 5.12: fully bound groundlink services."""

from dataclasses import dataclass

from crew_health.groundlink.application.agent import ContactAgent
from crew_health.groundlink.application.service import (
    GroundLinkCommands,
    GroundLinkDeps,
    GroundLinkReader,
    Inbox,
    Outbox,
)


@dataclass(frozen=True, slots=True)
class GroundLinkServices:
    deps: GroundLinkDeps
    reader: GroundLinkReader
    commands: GroundLinkCommands
    outbox: Outbox
    inbox: Inbox
    agent: ContactAgent
