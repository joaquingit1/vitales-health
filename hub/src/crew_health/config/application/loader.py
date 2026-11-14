from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from crew_health.config.application.ports import TextMatches
from crew_health.config.domain.checks import parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.text_rules import AmountRules
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import ActivationGuard, CandidateRelease, Finding, ReleaseCheck, ReleaseHandle
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import UnitOfWork


def required_contributors() -> frozenset[str]:
    return frozenset(
        {
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12.sensor",
            "12.effect",
            "12.kit",
            "13",
            "14",
            "15",
            "16.skills",
            "16.roles",
        }
    )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    errors: tuple[Finding, ...]
    warnings: tuple[Finding, ...]
    executed: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


class LoaderChecks:
    def __init__(self, *, maximum: int) -> None:
        if not 1 <= maximum <= 1024:
            raise ConfigInvalid("invalid contributor bound")
        self.maximum = maximum
        self.checks: dict[str, ReleaseCheck] = {}
        self.guards: dict[str, ActivationGuard] = {}
        self.frozen = False

    def register(self, check: ReleaseCheck) -> None:
        if (
            self.frozen
            or check.check_id in self.checks
            or len(self.checks) + len(self.guards) >= self.maximum
        ):
            raise ConfigInvalid("duplicate, frozen or excessive check")
        self.checks[check.check_id] = check

    def register_guard(self, guard: ActivationGuard) -> None:
        if (
            self.frozen
            or guard.check_id in self.guards
            or len(self.checks) + len(self.guards) >= self.maximum
        ):
            raise ConfigInvalid("duplicate, frozen or excessive guard")
        self.guards[guard.check_id] = guard

    def freeze(self) -> None:
        self.frozen = True


class ConfigLoader:
    def __init__(self, checks: LoaderChecks, *, max_document_bytes: int) -> None:
        if not 1 <= max_document_bytes <= 8388608:
            raise ConfigInvalid("document bound invalid")
        self.checks, self.max_document_bytes = checks, max_document_bytes

    def validate(
        self,
        candidate: CandidateRelease,
        *,
        origin: Literal["LIVE", "REPLAY", "SIM"],
        uow: UnitOfWork | None = None,
    ) -> ValidationResult:
        errors = self._integrity(candidate)
        warnings: list[Finding] = []
        executed = []
        for check_id in sorted(required_contributors() | self.checks.checks.keys()):
            check = self.checks.checks.get(check_id)
            if check is None:
                errors.append(Finding(check_id, None, "/", "mandatory contributor unavailable"))
                continue
            findings = check.check(candidate)
            executed.append(check_id)
            for finding in findings:
                if (check_id == "6" and finding.check_id == "6.unbound_trigger") or (
                    check_id == "15" and origin != "LIVE"
                ):
                    warnings.append(finding)
                else:
                    errors.append(finding)
        if uow is not None:
            for check_id, guard in sorted(self.checks.guards.items()):
                errors.extend(guard.check(uow, candidate))
                executed.append(check_id)
        return ValidationResult(tuple(errors), tuple(warnings), tuple(executed))

    def _integrity(self, candidate: CandidateRelease) -> list[Finding]:
        errors = [
            Finding("1", name, "/", "required document missing")
            for name in ConfigName
            if name not in candidate.documents
        ]
        for name, document in sorted(candidate.documents.items()):
            if (
                name != document.name
                or len(document.raw) > self.max_document_bytes
                or hashlib.sha256(document.raw).hexdigest() != document.version
            ):
                errors.append(Finding("1", name, "/", "document identity, hash or bound invalid"))
            try:
                parse_document(document.raw)
            except ConfigInvalid:
                errors.append(Finding("1", name, "/", "invalid JSON document"))
        return errors


class TextRulesService:
    def __init__(self, *, settings: HubSettings) -> None:
        self.rules = AmountRules(
            max_text_bytes=settings.integer("text_rules.max_text_bytes"),
            max_matches=settings.integer("text_rules.max_matches"),
        )

    def find_amounts(self, text: str, *, release: ReleaseHandle) -> TextMatches:
        require_id(release.release_id)
        release.document(ConfigName.ASSISTANT_POLICY)
        return TextMatches(release.release_id, "checked", self.rules.find(text))

    def find_medicines(self, text: str, *, release: ReleaseHandle) -> TextMatches:
        require_id(release.release_id)
        release.document(ConfigName.ASSISTANT_POLICY)
        if len(text.encode("utf-8")) > self.rules.max_text_bytes:
            raise ConfigInvalid("text byte bound exceeded")
        # R-E01-27 leaves the release-pinned medicine vocabulary unallocated.
        return TextMatches(release.release_id, "unavailable", ())
