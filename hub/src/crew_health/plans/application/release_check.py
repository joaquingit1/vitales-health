"""Candidate checks 14 and 15, with absent verification capabilities kept closed."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ConfigName, Finding
from crew_health.plans.application.release import kit_supplies, packed_stock, read_document
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.model import parse_document
from crew_health.plans.domain.release_checks import CatalogFacts, KitFacts, ReleaseFacts, check_plans


@dataclass(frozen=True, slots=True)
class _CandidateHandle:
    candidate: CandidateRelease

    @property
    def release_id(self) -> str:
        return self.candidate.release_id

    def document(self, name: ConfigName) -> ConfigDocumentView:
        return self.candidate.documents[name]

    def version_of(self, name: ConfigName) -> str:
        return self.document(name).version


def _objects(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("expected document array")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("expected document object")
        result.append(cast(Mapping[str, object], item))
    return tuple(result)


def _catalog(raw: Mapping[str, object]) -> CatalogFacts:
    skills = _objects(raw.get("skills", ()))
    landmarks = _objects(raw.get("landmarks", ()))
    texts: list[tuple[str, str]] = []
    for index, skill in enumerate(skills):
        for j, step in enumerate(_objects(skill.get("teach_steps", ()))):
            for field in ("text", "text_first_time"):
                text = step.get(field)
                if isinstance(text, str):
                    texts.append((f"/skills/{index}/teach_steps/{j}/{field}", text))
    return CatalogFacts(
        frozenset(str(s["skill_id"]) for s in skills),
        frozenset(str(s["landmark_id"]) for s in landmarks),
        tuple(texts),
    )


def _kit(raw: Mapping[str, object], handle: _CandidateHandle) -> KitFacts:
    kits = raw.get("kits", {})
    if not isinstance(kits, dict):
        raise ValueError("expected kits object")
    parsed: dict[str, frozenset[str]] = {}
    for environment, instruments in kits.items():
        if not isinstance(environment, str) or not isinstance(instruments, list):
            raise ValueError("invalid kit")
        if not all(isinstance(i, str) for i in instruments):
            raise ValueError("invalid instrument")
        parsed[environment] = frozenset(cast(Sequence[str], instruments))
    return KitFacts(
        frozenset(str(i["id"]) for i in _objects(raw.get("instruments", ()))),
        kit_supplies(handle),
        parsed,
        packed_stock(handle),
        tuple(
            (str(row["condition_id"]), cast(str | None, row.get("plan_id")))
            for row in _objects(raw.get("conditions", ()))
        ),
    )


class PlansReleaseCheck:
    check_id = "plans.onboard_plans"

    def __init__(
        self,
        settings: PlansSettings | Callable[[], PlansSettings] | None = None,
        mode_provider: Callable[[], Literal["LIVE", "REPLAY", "SIM"] | None] | None = None,
    ) -> None:
        self.settings = settings
        self.mode_provider = mode_provider
        self.warning_count = 0

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        self.warning_count = 0
        if ConfigName.ONBOARD_PLANS not in candidate.documents:
            return ()
        try:
            return self._check(candidate)
        except Exception as error:
            logging.getLogger(__name__).error("plans_release_check_failed %s", type(error).__name__)
            return (Finding(self.check_id, ConfigName.ONBOARD_PLANS, "", "plans check failed"),)

    def _check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        settings = self.settings() if callable(self.settings) else self.settings
        if settings is None:
            raise ValueError("plans settings unavailable")
        handle = _CandidateHandle(candidate)
        raw = read_document(handle, ConfigName.ONBOARD_PLANS)
        kit = read_document(handle, ConfigName.KIT_MAP)
        catalog = read_document(handle, ConfigName.SKILLS_CATALOG)
        profiles = read_document(handle, ConfigName.ENVIRONMENT_PROFILES)
        flows = read_document(handle, ConfigName.RESPONDER_FLOWS)
        if raw is None or kit is None or catalog is None or profiles is None or flows is None:
            raise ValueError("plans release dependency absent")
        document = handle.document(ConfigName.ONBOARD_PLANS)
        if len(document.raw) > settings.max_document_bytes:
            raise ValueError("plans document bound exceeded")
        parsed = parse_document(
            raw, document_sha256=hashlib.sha256(document.raw).hexdigest(), supplies=kit_supplies(handle)
        )
        mode = self.mode_provider() if self.mode_provider is not None else None
        facts = ReleaseFacts(
            _kit(kit, handle),
            _catalog(catalog),
            frozenset(str(f["id"]) for f in _objects(flows.get("flows", ()))),
            {
                str(p["environment_id"]): p.get("return_option") is not None
                for p in _objects(profiles.get("profiles", ()))
            },
            {},
            {},
            mode if mode is not None else "LIVE",
            settings.max_plans_per_document,
            settings.max_steps_per_plan,
        )
        findings = check_plans(parsed, raw, facts)
        self.warning_count = sum(f.severity == "warning" for f in findings)
        if self.warning_count:
            logging.getLogger(__name__).warning("plans_release_warnings count=%s", self.warning_count)
        return tuple(
            Finding(self.check_id, ConfigName.ONBOARD_PLANS, f.pointer, f.message)
            for f in findings
            if f.severity == "reject"
        )
