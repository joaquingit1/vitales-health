"""The help application assembled from its dependencies: one object the API and module registration use."""

from __future__ import annotations

from dataclasses import replace

from crew_health.help.application.acting import ActOnHelp
from crew_health.help.application.assistant_flow import AssistantSink
from crew_health.help.application.ground_flow import GroundMessages
from crew_health.help.application.opening import OpenHelp
from crew_health.help.application.optional import OptionalCalls
from crew_health.help.application.provided import (
    GroundMessageLifecycle,
    HelpEntry,
    LogSnapshots,
    SessionReader,
)
from crew_health.help.application.queries import HelpQueries
from crew_health.help.application.support import DocsCache, HelpDeps, bounds
from crew_health.help.application.worker import HelpWorker
from crew_health.kernel.config import ConfigReleaseReader


class HelpService:
    def __init__(self, deps: HelpDeps, releases: ConfigReleaseReader | None = None) -> None:
        deps = replace(deps, calls=OptionalCalls(bounds(deps).port_timeout_ms / 1000))
        self.deps = deps
        cache = DocsCache(deps.config, bounds(deps).docs_cache_entries)
        self.opening = OpenHelp(deps, cache, releases)
        self.acting = ActOnHelp(deps, cache)
        self.queries = HelpQueries(deps, cache)
        self.ground = GroundMessages(deps, cache)
        self.assistant = AssistantSink(deps, cache)
        self.reader = SessionReader(deps)
        self.entry = HelpEntry(self.opening)
        self.snapshots = LogSnapshots(deps)
        self.lifecycle = GroundMessageLifecycle(self.ground)
        self.worker = HelpWorker(deps, cache)
