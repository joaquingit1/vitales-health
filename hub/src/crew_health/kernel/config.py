from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

# Re-exported for pure domain code (SYSTEM 6.4 domain-pure).
from crew_health.contracts_gen.enums import ConfigName as ConfigName

if TYPE_CHECKING:
    from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork


@dataclass(frozen=True, slots=True)
class ConfigDocumentView:
    name: ConfigName
    version: str
    raw: bytes


class ReleaseHandle(Protocol):
    @property
    def release_id(self) -> str: ...
    def document(self, name: ConfigName) -> ConfigDocumentView: ...
    def version_of(self, name: ConfigName) -> str: ...


@dataclass(frozen=True, slots=True)
class CandidateRelease:
    release_id: str
    documents: Mapping[ConfigName, ConfigDocumentView]

    def __post_init__(self) -> None:
        object.__setattr__(self, "documents", _FrozenMapping(self.documents))


class ConfigReleaseReader(Protocol):
    def active(self) -> ReleaseHandle: ...
    def release(self, release_id: str) -> ReleaseHandle: ...


class ConfigReader(Protocol):
    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView: ...


@dataclass(frozen=True, slots=True)
class Finding:
    check_id: str
    document: ConfigName | None
    pointer: str
    message: str


class ReleaseCheck(Protocol):
    @property
    def check_id(self) -> str: ...
    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]: ...


class ActivationGuard(Protocol):
    @property
    def check_id(self) -> str: ...
    def check(self, uow: UnitOfWork, candidate: CandidateRelease) -> tuple[Finding, ...]: ...


class LoaderCheckRegistry(Protocol):
    def register(self, check: ReleaseCheck) -> None: ...
    def register_guard(self, guard: ActivationGuard) -> None: ...


class TemplateRenderer(Protocol):
    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str: ...


class BoundConfig(ConfigReleaseReader, ConfigReader, TemplateRenderer, Protocol):
    """The configuration readers bound to one UnitOfWork or ReadSnapshot (SYSTEM 5.6). `active()` and an unpinned
    `document(name)` answer the release that view saw at its start, so one command sees one active release however
    many reads it makes; `render` fills a missing value with that release's unknown text."""


class ConfigBinding(Protocol):
    def bind(self, view: ReadSnapshot | UnitOfWork) -> BoundConfig: ...


@dataclass(frozen=True, slots=True)
class ReleaseRevision:
    release_id: str
    revision: int
    etag: str


class ReleaseActivation(Protocol):
    def revision(self, snapshot: ReadSnapshot, release_id: str) -> ReleaseRevision: ...
    def activate(
        self, uow: UnitOfWork, session: SessionContext, *, release_id: str, expected_etag: str
    ) -> ReleaseRevision: ...


@dataclass(frozen=True, slots=True)
class _FrozenMapping[K, V](Mapping[K, V]):
    _items: tuple[tuple[K, V], ...]

    def __init__(self, values: Mapping[K, V]) -> None:
        object.__setattr__(self, "_items", tuple(values.items()))

    def __getitem__(self, key: K) -> V:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)
