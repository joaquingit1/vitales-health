"""TapBeat (E30 3.3): one unit of work per beat, so concurrent taps serialize. Idempotency first, then attempt
binding, sequence, window, capacity. The two refusals that mark the attempt incomplete commit that mark and then
return 409; every other refusal writes nothing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.errors import ProblemError
from crew_health.teach.application import access
from crew_health.teach.application.commands import CommandMeta, Response, TeachCore
from crew_health.teach.application.queries import instant
from crew_health.teach.domain.errors import BeatRefused, TeachError

OPERATION = "tapBeat"


@dataclass(frozen=True, slots=True)
class BeatInput:
    sequence: int
    client_time: datetime


class TapBeat(TeachCore):
    def __call__(self, meta: CommandMeta, guided_check_id: str, data: BeatInput) -> Response:
        identity = self.identity(meta, OPERATION, guided_check_id)
        body_sha256 = hashlib.sha256(meta.body).hexdigest()
        refusal: ProblemError | None = None
        with self.unit(meta, identity) as uow:
            replay = self.replay(uow, identity, meta.session)
            if replay is not None:
                return replay
            check = self.load(uow, guided_check_id)
            access.authorize(meta.session, check, "beat")
            r = self.reading()
            settings = self.p.settings
            try:
                beat = check.accept_beat(
                    data.sequence,
                    body_sha256,
                    data.client_time,
                    r,
                    max_beats_per_second=settings.max_beats_per_second,
                    clock_tolerance_ms=settings.clock_tolerance_ms,
                )
            except BeatRefused as error:
                if not error.recorded:
                    raise self.refuse(uow, guided_check_id, meta.session, error) from error
                self.save(uow, check)
                self.audit(uow, meta.session, "beat_refused", check)
                refusal = self.refuse(uow, guided_check_id, meta.session, error)
            except TeachError as error:
                raise self.refuse(uow, guided_check_id, meta.session, error) from error
            if refusal is None:
                if beat is not None:
                    attempt = check.attempt
                    window = attempt.window if attempt is not None else None
                    self.save(uow, check)
                    data_out = {
                        "guided_check_id": check.guided_check_id,
                        "beat_count": len(check.visible_beats()),
                        "at": instant(beat.at_utc),
                        "seconds_left": 0 if window is None else window.seconds_left(r),
                    }
                    self.emit(uow, check, EventType.GUIDED_CHECK_BEAT, data_out, beat.at_utc)
                    self.audit(uow, meta.session, "beat_accepted", check)
                self.remember(uow, identity, check, 200)
                return self.response(check, meta.session, 200, r)
        if refusal is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        raise refusal
