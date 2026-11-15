"""getGuidedCheck, the pinned steps (X1 interim route), listCoachingSamples and the coaching sample recorder
(E30 3.7, 4.2). Reads authorize per object; coaching samples are readable exactly when E26 says the help session
is (HelpSessionReader.readable_by)."""

from __future__ import annotations

from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    SessionContext,
    UnitOfWork,
)
from crew_health.teach.application import access
from crew_health.teach.application.commands import CommandMeta, Response, TeachCore
from crew_health.teach.application.content import PHRASE_FIELD
from crew_health.teach.application.ports import CoachingSampleRepository, HelpSessionReader, StoredSample
from crew_health.teach.application.queries import instant, number
from crew_health.teach.domain.agreement import rounded
from crew_health.teach.domain.coaching import RawRateSample, accept_raw, derive
from crew_health.teach.domain.errors import ContextInapplicable

COACHING_RULE = "teach.coaching_readers"
COACHING_PROJECTION = AudienceProjectionKey("teach", "coaching-sample", 1)
CURSOR_SEPARATOR = "|"


class GetGuidedCheck(TeachCore):
    def __call__(self, meta: CommandMeta, guided_check_id: str) -> Response:
        with self.unit(meta, None) as uow:
            check = self.load(uow, guided_check_id)
            access.authorize(meta.session, check, "read")
            return self.response(check, meta.session, 200)

    def rendered(self, meta: CommandMeta, guided_check_id: str) -> dict[str, object]:
        with self.unit(meta, None) as uow:
            check = self.load(uow, guided_check_id)
            access.authorize(meta.session, check, "read")
            return self.steps(check)


def sample_wire(sample: StoredSample) -> dict[str, object]:
    return {
        "sample_id": sample.sample_id,
        "help_session_id": sample.help_session_id,
        "drill_id": None,
        "skill_id": sample.skill_id,
        "check_id": sample.check_id,
        "at": instant(sample.at),
        "kind": "compression_rate",
        "rate_per_min": None if sample.rate_per_min is None else number(sample.rate_per_min),
        "reason": sample.reason,
        "in_band": sample.in_band,
        "direction": sample.direction,
        "text": sample.text,
        "experimental": sample.experimental,
        "persisted": True,
    }


class CoachingSamples(TeachCore):
    """listCoachingSamples and the recorder the coaching coordinator calls (the coordinator itself is a gap)."""

    def __init__(
        self, core: TeachCore, samples: CoachingSampleRepository, help: HelpSessionReader | None
    ) -> None:
        super().__init__(core.p)
        self._samples = samples
        self._help = help

    def page(
        self, session: SessionContext, help_session_id: str, cursor: str | None, limit: int | None
    ) -> dict[str, object]:
        size = self.p.settings.list_page_max if limit is None else limit
        if not 1 <= size <= self.p.settings.list_page_max:
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        if self._help is None or not self._help.readable_by(session, help_session_id):
            raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
        after = _parse_cursor(cursor)
        with self.p.snapshots.snapshot() as view:
            items = self._samples.page(view, help_session_id, after=after, limit=size + 1)
        shown = list(items[:size])
        more = len(items) > size
        next_cursor = _cursor(shown[-1]) if more and shown else None
        return {"items": [sample_wire(s) for s in shown], "page": {"next_cursor": next_cursor}}

    def record(
        self, uow: UnitOfWork, help_session_id: str, skill_id: str, raw: RawRateSample, pins: dict[str, str]
    ) -> StoredSample | None:
        """Derive, store and emit one sample; a stale, uncertain or out of order raw sample is dropped (3.7)."""
        content = self.p.content.load(pins)
        skill = content.skill(skill_id)
        check = skill.compression_check
        if check is None:
            raise ContextInapplicable("skill_without_compression_check")
        last = self._samples.last_at(uow, help_session_id, check.check_id)
        if not accept_raw(
            raw,
            uow.now,
            check,
            max_uncertainty_ms=self.p.settings.camera_clock_tolerance_ms,
            last_captured=last,
        ):
            return None
        derived = derive(raw, check)
        phrase = check.phrases.get(derived.phrase_key)
        if not phrase:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=False)
        rate = None if derived.rate is None else rounded(derived.rate, self.p.settings.rate_display_decimals)
        text = self.p.renderer.render(PHRASE_FIELD, phrase, {} if rate is None else {"rate": str(rate)})
        sample = StoredSample(
            sample_id=self.p.ids.new("cs"),
            help_session_id=help_session_id,
            skill_id=skill_id,
            check_id=check.check_id,
            at=raw.captured_utc,
            rate_per_min=rate,
            reason=derived.reason,
            in_band=derived.in_band,
            direction=derived.direction,
            text=text,
            experimental=check.experimental,
        )
        release = self.p.releases.active().release_id
        self._samples.add(uow, sample, release_id=release)
        audience = Audience(COACHING_RULE, help_session_id, COACHING_PROJECTION, "teach-access-1")
        uow.emit(
            OutboxEvent(
                type=EventType.COACHING_SAMPLE,
                occurred_at=sample.at,
                scope=EventScope("crew", None, None),
                config_release_id=release,
                correlation_id=uow.correlation_id,
                data_json=canonical_json(sample_wire(sample)),
                audience=audience,
            )
        )
        return sample


def _cursor(sample: StoredSample) -> str:
    return instant(sample.at) + CURSOR_SEPARATOR + sample.sample_id


def _parse_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    at, separator, sample_id = cursor.partition(CURSOR_SEPARATOR)
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value) from error
    if not separator or not sample_id or when.tzinfo is None:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    return when, sample_id
