from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.contracts_gen.schemas.event_page_schema import EventPage
from crew_health.events.adapters.notifier import BoundedNotifier, WakeSubscription
from crew_health.events.application.delivery import EventDelivery
from crew_health.events.domain.cursor import event_types, select_cursor
from crew_health.events.domain.errors import failure
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import SessionContext


def request_session(request: Request) -> SessionContext:
    value: object = getattr(request.state, "session", None)
    if not isinstance(value, SessionContext):
        raise failure(ErrorCode.UNAUTHENTICATED)
    return value


async def wait_for_wake(lease: WakeSubscription, seconds: int) -> None:
    await asyncio.wait_for(lease.queue.get(), timeout=seconds)
    lease.queue.task_done()


async def stream_events(
    *,
    delivery: EventDelivery,
    lease: WakeSubscription,
    settings: HubSettings,
    session: SessionContext,
    initial: EventPage,
    disconnected: Callable[[], Awaitable[bool]],
    types: frozenset[EventType] | None = None,
    wait: Callable[[WakeSubscription, int], Awaitable[None]] = wait_for_wake,
) -> AsyncIterator[bytes]:
    page = initial
    try:
        while not await disconnected():
            for event in page.events:
                current = delivery.current_event(session, event.seq)
                if current is not None:
                    yield f"id: {current.seq}\nevent: {current.type}\ndata: {current.model_dump_json()}\n\n".encode()
            if page.has_more:
                await asyncio.sleep(0)
            else:
                try:
                    await wait(lease, settings.integer("events.heartbeat_seconds"))
                except TimeoutError:
                    with delivery.snapshots.snapshot() as snapshot:
                        delivery.require_current(snapshot, session)
                    yield b": heartbeat\n\n"
            page = delivery.page(session, after=page.next_cursor, types=types)
    except ProblemError as error:
        if error.code not in (ErrorCode.UNAUTHENTICATED, ErrorCode.SESSION_CLOSED):
            raise
    finally:
        lease.close()


def create_router(
    *,
    delivery: EventDelivery,
    notifier: BoundedNotifier,
    settings: HubSettings,
    session_provider: Callable[[Request], SessionContext] = request_session,
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/events", operation_id="getEvents")
    async def get_events(request: Request) -> Response:
        session = session_provider(request)
        after = select_cursor(request.query_params.get("after"), request.headers.get("Last-Event-ID"))
        types = event_types(request.query_params.get("types"))
        page = delivery.page(session, after=after, types=types)
        if "application/json" in request.headers.get("Accept", ""):
            return JSONResponse(page.model_dump(mode="json"))
        lease = notifier.subscribe()
        return StreamingResponse(
            stream_events(
                delivery=delivery,
                lease=lease,
                settings=settings,
                session=session,
                initial=page,
                disconnected=request.is_disconnected,
                types=types,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return router
