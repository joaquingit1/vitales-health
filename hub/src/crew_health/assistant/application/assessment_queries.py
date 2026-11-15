from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import ValidationError

from crew_health.assistant.application.access import Access
from crew_health.assistant.application.dto import Assessment
from crew_health.assistant.application.json_data import JsonBounds, parse
from crew_health.assistant.application.ports import DeliveredAssessmentSource, SelectedAssessment
from crew_health.assistant.application.state_ports import State, View
from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.assessment_schema import Assessment as WireAssessment
from crew_health.contracts_gen.schemas.common_schema import Instant
from crew_health.kernel.clock import require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import SessionContext

FIELDS = frozenset(WireAssessment.model_fields)
IDENTITY_FIELDS = frozenset(
    {
        "assessment_id",
        "episode_id",
        "origin",
        "state_snapshot_version",
        "evidence_release_id",
        "model_release_id",
        "validation_status",
        "created_at",
    }
)


class AssessmentQueries:
    def __init__(
        self, state: State, access: Access, *, delivered: DeliveredAssessmentSource | None, max_bytes: int
    ) -> None:
        self.state, self.access, self.delivered, self.max_bytes = state, access, delivered, max_bytes

    def get(self, view: View, session: SessionContext, assessment_id: str) -> Mapping[str, object]:
        require_id(assessment_id)
        if session.kind == "ground":
            return self._ground(view, session, assessment_id)
        value = self._local(view, session, assessment_id, fields=FIELDS)
        return self._current_body(value)

    def _local(
        self, view: View, session: SessionContext, assessment_id: str, *, fields: frozenset[str]
    ) -> Assessment:
        value = self.state.assessment(view, assessment_id)
        if value is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found")
        self.access.assessment(
            view, session, person_id=value.person_id, assessment_id=assessment_id, fields=fields
        )
        return value

    def _current_body(self, value: Assessment) -> dict[str, object]:
        body = self._public(value.body)
        if body["assessment_id"] != value.assessment_id or body["episode_id"] != value.episode_id:
            raise StorageUnavailable("corrupt")
        body["validation_status"] = value.validation_status
        return body

    def read(
        self, view: View, session: SessionContext, *, assessment_id: str, fields: frozenset[str]
    ) -> SelectedAssessment:
        require_id(assessment_id)
        if not fields or not fields <= FIELDS:
            raise ProblemError(ErrorCode.INVALID_VALUE, "assessment fields invalid")
        value = self._local(view, session, assessment_id, fields=fields | IDENTITY_FIELDS)
        body = self._current_body(value)
        raw = canonical_json({key: body[key] for key in sorted(fields)})
        return SelectedAssessment(
            value.assessment_id,
            value.episode_id,
            value.person_id,
            value.revision,
            value.validation_status,
            value.config_release_id,
            cast(str, body["evidence_release_id"]),
            cast(str | None, body["model_release_id"]),
            fields,
            hashlib.sha256(raw).hexdigest(),
            raw,
            value.origin_mode,
            value.created_at,
        )

    def _ground(self, view: View, session: SessionContext, assessment_id: str) -> Mapping[str, object]:
        if session.actor.kind != "ground" or Role.GROUND not in session.roles:
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found")
        self.access.current(view, session)
        if self.delivered is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "delivered assessment source unavailable")
        try:
            source = self.delivered.read(view, session, assessment_id=assessment_id, fields=FIELDS)
        except ProblemError as error:
            if error.code not in {
                ErrorCode.UNAUTHORIZED_SCOPE,
                ErrorCode.CONSENT_REQUIRED,
                ErrorCode.ACTION_NOT_ALLOWED,
            }:
                raise
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found") from None
        if source is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found")
        if (
            source.assessment_id != assessment_id
            or source.recipient_id != session.actor.id
            or source.selected_fields != FIELDS
            or type(source.source_revision) is not int
            or source.source_revision < 1
            or source.origin_mode not in {"LIVE", "REPLAY", "SIM"}
            or len(source.body_json) > self.max_bytes
            or hashlib.sha256(source.body_json).hexdigest() != source.content_sha256
        ):
            raise ProblemError(ErrorCode.SOURCE_NOT_APPROVED, "delivered assessment invalid")
        for ref in (
            source.handoff_id,
            source.delivery_id,
            source.subject_person_id,
            source.config_release_id,
            source.evidence_release_id,
            source.episode_id,
        ):
            require_id(ref)
        require_utc(source.delivered_at)
        require_utc(source.created_at)
        try:
            body = self._public(source.body_json)
        except StorageUnavailable:
            raise ProblemError(ErrorCode.SOURCE_NOT_APPROVED, "delivered assessment invalid") from None
        if (
            body["assessment_id"] != assessment_id
            or body["episode_id"] != source.episode_id
            or body["evidence_release_id"] != source.evidence_release_id
            or body["model_release_id"] != source.model_release_id
            or body["validation_status"] != source.validation_status
            or datetime.fromisoformat(cast(str, body["created_at"])) != source.created_at
        ):
            raise ProblemError(ErrorCode.SOURCE_NOT_APPROVED, "delivered assessment invalid")
        return body

    def _public(self, raw: bytes) -> dict[str, object]:
        try:
            # RFC8785's own depth ceiling; node and string growth are bounded by the supplied byte budget.
            value = parse(raw, JsonBounds(self.max_bytes, 64, self.max_bytes, self.max_bytes, self.max_bytes))
            if not isinstance(value, dict) or not isinstance(value.get("created_at"), str):
                raise ValueError("assessment instant invalid")
            instant = datetime.fromisoformat(cast(str, value["created_at"]))
            require_utc(instant)
            materialized: dict[str, object] = dict(value)
            materialized["created_at"] = Instant.model_validate(value["created_at"])
            WireAssessment.model_validate(materialized, strict=True)
            return cast(dict[str, object], dict(value))
        except (ProblemError, ValidationError, ValueError) as error:
            raise StorageUnavailable("corrupt") from error
