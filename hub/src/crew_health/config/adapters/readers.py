"""Configuration readers (SYSTEM 5.6).

`ConfigReaders.bind(view)` returns readers bound to the caller's UnitOfWork or ReadSnapshot: `active()` is the release
that view pinned at its start, so one command sees one active release however many reads it makes, even when another
connection activates a release meanwhile. The process-level methods serve callers outside any command (an error
envelope, a pinned version): each call opens one read snapshot, and a pinned version or release id always returns the
same immutable bytes.

The renderer fills a missing placeholder with the ui-copy `designed_states.unknown` text of the release it reads
(CONTRACTS 11) and logs the field key only; a release without that text fails the render instead of inventing words.
That text is parsed once per ui-copy version (the sha256 digest of its bytes) and kept in a cache bounded by the
`config.ui_copy_cache_max` hub setting."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass

from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader
from crew_health.config.domain.checks import parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.templates import PinnedTemplateRenderer
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ConfigDocumentView, ReleaseHandle
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, UnitOfWork

LOGGER = "crew_health.config.templates"
UI_COPY_CACHE_SETTING = "config.ui_copy_cache_max"


def unknown_text_of(document: ConfigDocumentView) -> str:
    parsed = parse_document(document.raw)
    states = parsed.get("designed_states") if isinstance(parsed, dict) else None
    value = states.get("unknown") if isinstance(states, dict) else None
    if not isinstance(value, str) or not value:
        raise ConfigInvalid("ui-copy designed_states.unknown unavailable")
    return value


class UnknownTextCache:
    """The parsed unknown text per ui-copy version; least recently used entries leave first. Safe across threads."""

    def __init__(self, maximum: int) -> None:
        if maximum < 1:
            raise ConfigInvalid("ui-copy cache bound must be positive")
        self._maximum = maximum
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, str] = OrderedDict()

    def text(self, release: ReleaseHandle) -> str:
        document = release.document(ConfigName.UI_COPY)
        with self._lock:
            cached = self._entries.get(document.version)
            if cached is not None:
                self._entries.move_to_end(document.version)
                return cached
        value = unknown_text_of(document)
        with self._lock:
            self._entries[document.version] = value
            self._entries.move_to_end(document.version)
            while len(self._entries) > self._maximum:
                self._entries.popitem(last=False)
        return value


def _log_missing(field_key: str) -> None:
    logging.getLogger(LOGGER).warning("template value missing: field_key=%s", field_key)


def _render(
    copies: UnknownTextCache, release: ReleaseHandle, field_key: str, template: str, values: Mapping[str, str]
) -> str:
    return PinnedTemplateRenderer(copies.text(release), _log_missing).render(field_key, template, values)


@dataclass(frozen=True, slots=True)
class BoundConfigReader:
    """ConfigReleaseReader, ConfigReader and TemplateRenderer over one caller's view (kernel BoundConfig)."""

    view: ReadSnapshot | UnitOfWork
    repository: ConfigRepository
    copies: UnknownTextCache

    def active(self) -> ReleaseHandle:
        return self.view.release

    def release(self, release_id: str) -> ReleaseHandle:
        return self.repository.release(self.view, release_id)

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        if version is None:
            return self.view.release.document(name)
        return SQLiteConfigReader(self.view, self.repository).document(name, version)

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        return _render(self.copies, self.view.release, field_key, template, values)


@dataclass(frozen=True, slots=True)
class ConfigReaders:
    """The process-level provider: `bind` for commands and reads that hold a view, one snapshot per call otherwise."""

    snapshots: ReadSnapshotFactory
    repository: ConfigRepository
    copies: UnknownTextCache

    def bind(self, view: ReadSnapshot | UnitOfWork) -> BoundConfigReader:
        return BoundConfigReader(view, self.repository, self.copies)

    def active(self) -> ReleaseHandle:
        with self.snapshots.snapshot() as view:
            return view.release

    def release(self, release_id: str) -> ReleaseHandle:
        with self.snapshots.snapshot() as view:
            return self.repository.release(view, release_id)

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        with self.snapshots.snapshot() as view:
            return self.bind(view).document(name, version)

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        with self.snapshots.snapshot() as view:
            return self.bind(view).render(field_key, template, values)
