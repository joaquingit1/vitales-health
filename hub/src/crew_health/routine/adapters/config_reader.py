import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.config_questionnaires_schema import QuestionnairesJson
from crew_health.contracts_gen.schemas.config_routine_rules_schema import RoutineRulesJson
from crew_health.contracts_gen.schemas.config_routine_schedule_schema import RoutineScheduleJson
from crew_health.contracts_gen.schemas.config_ui_copy_schema import UiCopyJson
from crew_health.kernel.config import (
    CandidateRelease,
    ConfigDocumentView,
    ConfigName,
    ConfigReleaseReader,
    Finding,
    ReleaseHandle,
)
from crew_health.kernel.errors import ProblemError
from crew_health.routine.application.settings import specs
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.errors import Finding as DomainFinding
from crew_health.routine.domain.loader_checks import Bounds, check_release
from crew_health.routine.domain.release import RoutineRelease, parse_release


def _document(
    handle: ReleaseHandle, name: ConfigName, model: type[BaseModel], *, optional: bool = False
) -> Mapping[str, object] | None:
    try:
        document = handle.document(name)
    except ProblemError as error:
        if error.code != ErrorCode.CONFIG_INVALID:
            raise
        if optional:
            return None
        raise ConfigInvalid(
            (DomainFinding("error", "/" + name.value, "Required routine document is missing"),)
        ) from error
    try:
        model.model_validate_json(document.raw)
        decoded: object = json.loads(document.raw)
        if not isinstance(decoded, dict):
            raise ValueError("Expected an object")
        return cast(Mapping[str, object], decoded)
    except (ValidationError, ValueError, UnicodeError) as error:
        raise ConfigInvalid(
            (DomainFinding("error", "/" + name.value, "Document does not match its generated schema"),)
        ) from error


def parse_documents(handle: ReleaseHandle, bounds: Bounds) -> RoutineRelease:
    schedule = _document(handle, ConfigName.ROUTINE_SCHEDULE, RoutineScheduleJson)
    rules = _document(handle, ConfigName.ROUTINE_RULES, RoutineRulesJson)
    if schedule is None or rules is None:
        raise ConfigInvalid((DomainFinding("error", "/routine", "Required documents are missing"),))
    ui = _document(handle, ConfigName.UI_COPY, UiCopyJson, optional=True)
    questions = _document(handle, ConfigName.QUESTIONNAIRES, QuestionnairesJson, optional=True)
    release = parse_release(handle.release_id, schedule, rules, ui, questions)
    errors = tuple(f for f in check_release(release, bounds, None, None) if f.severity == "error")
    if errors:
        raise ConfigInvalid(errors)
    return release


class ReleaseCache:
    def __init__(
        self, capacity: int, bounds: Bounds | None = None, history: ConfigReleaseReader | None = None
    ) -> None:
        if not 1 <= capacity <= 64:
            raise ConfigInvalid(
                (
                    DomainFinding(
                        "error", "/routine/release_cache_entries", "Cache capacity outside registered bounds"
                    ),
                )
            )
        maxima = {spec.key: int(spec.maximum) for spec in specs() if spec.maximum is not None}
        self.capacity = capacity
        self.bounds = bounds or Bounds(
            maxima["routine.max_items"],
            maxima["routine.max_bindings"],
            maxima["routine.max_slots_per_binding"],
        )
        self.history = history
        self._entries: OrderedDict[str, RoutineRelease] = OrderedDict()

    def cached(self, release_id: str) -> RoutineRelease | None:
        value = self._entries.get(release_id)
        if value is not None:
            self._entries.move_to_end(release_id)
        return value

    def pinned(self, release_id: str) -> RoutineRelease | None:
        """The release open work was created under: from the cache, else re-read through the kernel release reader
        (retained release versions survive a restart, SYSTEM 5.6); None when neither can serve it."""
        value = self.cached(release_id)
        if value is not None or self.history is None:
            return value
        try:
            return self.release(self.history.release(release_id))
        except ProblemError, ConfigInvalid:
            return None

    def release(self, handle: ReleaseHandle) -> RoutineRelease:
        value = self.cached(handle.release_id)
        if value is not None:
            return value
        value = parse_documents(handle, self.bounds)
        self._entries[handle.release_id] = value
        if len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return value


@dataclass(frozen=True, slots=True)
class _CandidateHandle:
    release_id: str
    documents: Mapping[ConfigName, ConfigDocumentView]

    def document(self, name: ConfigName) -> ConfigDocumentView:
        value = self.documents.get(name)
        if value is None:
            raise ProblemError(ErrorCode.CONFIG_INVALID, "Missing candidate document")
        return value

    def version_of(self, name: ConfigName) -> str:
        return self.document(name).version


@dataclass(frozen=True, slots=True)
class RoutineReleaseCheck:
    bounds: Bounds
    check_id: str = "routine.release"

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        try:
            parse_documents(_CandidateHandle(candidate.release_id, candidate.documents), self.bounds)
        except ConfigInvalid as error:
            return tuple(
                Finding(self.check_id, _finding_document(f.pointer), f.pointer, f.message)
                for f in error.findings
            )
        return ()


def _finding_document(pointer: str) -> ConfigName | None:
    if pointer.startswith("/routine-schedule"):
        return ConfigName.ROUTINE_SCHEDULE
    if pointer.startswith("/routine-rules"):
        return ConfigName.ROUTINE_RULES
    return None
