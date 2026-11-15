"""CONTRACTS 12: each transport step commits separately; no owner call holds a transaction."""

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from crew_health.groundlink.application.owner import OwnerCalls
from crew_health.groundlink.application.ports import GroundLinkView
from crew_health.groundlink.application.repository import Ack
from crew_health.groundlink.application.service import GroundLinkDeps, GroundLinkReader, emit_link
from crew_health.groundlink.domain.queue import Bundle, backoff_ms, to_arrive, to_send
from crew_health.kernel.uow import Actor


@dataclass(frozen=True, slots=True)
class AgentPass:
    observed: int = 0
    sent: int = 0
    arrived: int = 0
    inbound: int = 0


class ContactAgent:
    def __init__(self, deps: GroundLinkDeps) -> None:
        self.deps = deps
        self.reader = GroundLinkReader(deps)
        self.owner = OwnerCalls(deps.settings.port_timeout_ms)
        self.lock = threading.Lock()

    def run_once(self) -> AgentPass:
        if not self.lock.acquire(blocking=False):
            return AgentPass()
        try:
            link, observed = self._observe()
            if link is None:
                return AgentPass()
            sent = self._send(link)
            arrived = self._arrive()
            inbound = self._inbound(link)
            # Contact timestamps are link changes too.
            _, contacts_observed = self._observe()
            return AgentPass(observed + contacts_observed, sent, arrived, inbound)
        finally:
            self.lock.release()

    def _observe(self) -> tuple[GroundLinkView | None, int]:
        with self.deps.uow.begin(
            actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
        ) as uow:
            link = self.reader.current(uow)
            history = self.deps.repository.history(uow)
            if link is None or (history and history[-1].link == link):
                return link, 0
            at = uow.now
            if link.source == "schedule":
                cfg = self.deps.config.get(uow, link.environment_id)
                boundary = max(
                    (b for w in cfg.profile.windows for b in (w.starts_at, w.ends_at) if b <= uow.now),
                    default=None,
                )
                if boundary is not None:
                    # A contact update does not rewrite the instant of a past transition.
                    at = boundary if not history or history[-1].at < boundary else uow.now
            self.deps.repository.append(uow, "observed", at, link)
            emit_link(uow, link)
            return link, 1

    def _retry_at(self, attempts: int, now: datetime) -> datetime:
        s = self.deps.settings
        return now + timedelta(
            milliseconds=backoff_ms(attempts, s.retry_backoff_ms, s.retry_max, s.backoff_cap_ms)
        )

    def _send(self, link: GroundLinkView) -> int:
        with self.deps.uow.begin(
            actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
        ) as uow:
            candidates = to_send(
                self.deps.repository.send_candidates(uow),
                link.status,
                uow.now,
                self.deps.settings.max_inflight,
            )
            pending = tuple(
                replace(
                    b,
                    state="sending",
                    attempts=b.attempts + 1,
                    next_attempt_at=self._retry_at(b.attempts + 1, uow.now),
                )
                for b in candidates
            )
            for bundle in pending:
                self.deps.repository.save_bundle(uow, bundle)
            now = uow.now
        count = 0
        failed = False
        for original, bundle in zip(candidates, pending, strict=True):
            error = None if failed else self._sent_call(bundle, link, now)
            with self.deps.uow.begin(
                actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
            ) as uow:
                if failed:
                    self.deps.repository.save_bundle(uow, original)
                    continue
                updated = replace(bundle, last_error=error)
                if error is None:
                    updated = replace(
                        updated,
                        state="sent",
                        sent_at=now,
                        one_way_delay_s=link.one_way_delay_s,
                        arrival_due_at=now + timedelta(seconds=link.one_way_delay_s or 0),
                        next_attempt_at=None,
                    )
                    count += 1
                self.deps.repository.save_bundle(uow, updated)
                failed = error is not None
        return count

    def _sent_call(self, bundle: Bundle, link: GroundLinkView, now: datetime) -> str | None:
        if bundle.ref_kind == "handoff":
            return None
        lifecycle = self.deps.lifecycle
        if lifecycle is None:
            return "owner_unavailable"
        event_id = self.deps.ids.derived("gle", bundle.bundle_id, "sent")
        return self.owner.call(
            event_id,
            lambda: lifecycle.mark_sent(
                bundle.ref_id,
                event_id,
                sent_at=now,
                one_way_delay_s=link.one_way_delay_s,
                simulated=bundle.simulated,
            ),
        )

    def _arrive(self) -> int:
        with self.deps.snapshots.snapshot() as view:
            bundles = to_arrive(self.deps.repository.arrival_candidates(view), view.now)
        count = 0
        for bundle in bundles:
            due = bundle.arrival_due_at
            if due is None:
                continue
            error = self._arrival_call(bundle, due)
            with self.deps.uow.begin(
                actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
            ) as uow:
                updated = (
                    replace(bundle, state="arrived", arrived_at=due, next_attempt_at=None, last_error=None)
                    if error is None
                    else replace(
                        bundle,
                        attempts=bundle.attempts + 1,
                        next_attempt_at=self._retry_at(bundle.attempts + 1, uow.now),
                        last_error=error,
                    )
                )
                self.deps.repository.save_bundle(uow, updated)
            count += error is None
        return count

    def _arrival_call(self, bundle: Bundle, due: datetime) -> str | None:
        if bundle.ref_kind == "handoff":
            return None
        lifecycle = self.deps.lifecycle
        if lifecycle is None:
            return "owner_unavailable"
        event_id = self.deps.ids.derived("gle", bundle.bundle_id, "arrived")
        return self.owner.call(
            event_id, lambda: lifecycle.record_arrival(bundle.ref_id, event_id, arrived_at=due)
        )

    def _inbound(self, link: GroundLinkView) -> int:
        if link.status == "down":
            return 0
        with self.deps.uow.begin(
            actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
        ) as uow:
            pending = []
            for ack in self.deps.repository.inbound_candidates(uow):
                if ack.state == "waiting":
                    ack = replace(
                        ack,
                        state="due",
                        arrival_due_at=uow.now + timedelta(seconds=link.one_way_delay_s or 0),
                    )
                    self.deps.repository.save_ack(uow, ack)
                if ack.arrival_due_at is not None and ack.arrival_due_at <= uow.now:
                    pending.append(ack)
        count = 0
        for ack in pending:
            error = self._inbound_call(ack)
            with self.deps.uow.begin(
                actor=Actor("engine", "groundlink"), correlation_id=self.deps.ids.new("glc")
            ) as uow:
                updated = (
                    replace(
                        ack,
                        state="delivered",
                        delivered_at=ack.arrival_due_at,
                        last_error=None,
                        next_attempt_at=None,
                    )
                    if error is None
                    else replace(
                        ack,
                        attempts=ack.attempts + 1,
                        next_attempt_at=self._retry_at(ack.attempts + 1, uow.now),
                        last_error=error,
                    )
                )
                self.deps.repository.save_ack(uow, updated)
            count += error is None
        return count

    def _inbound_call(self, ack: Ack) -> str | None:
        lifecycle, item, due = self.deps.lifecycle, ack.item, ack.arrival_due_at
        if lifecycle is None or item.ref_kind != "ground_message" or due is None:
            return "owner_unavailable"
        if item.kind == "receipt":
            return self.owner.call(
                item.event_id,
                lambda: lifecycle.record_receipt(
                    item.ref_id, item.event_id, received_at=item.sent_by_ground_at, arrived_at=due
                ),
            )
        if item.text is None:
            return "invalid_reply"
        return self.owner.call(
            item.event_id,
            lambda: lifecycle.record_reply(
                item.ref_id,
                item.event_id,
                item.text or "",
                item.by,
                replied_at=item.sent_by_ground_at,
                arrived_at=due,
            ),
        )
