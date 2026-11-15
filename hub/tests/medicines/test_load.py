"""E32-T-63 (HZ16, E32 11): medicines writes must not starve reads. For a fixed short window, one thread records
10 medicines writes per second (inspections and context statements, each its own Idempotency-Key) while the test
thread times medicines reads against the same file-backed station.

Limits of this harness: it composes neither the supplies module (which pins another release: the example kit map and
plans) nor a records module, so the 10 supply uses per second and the record entry timing of E32-T-63 are not
covered here; the medicines read is."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from .harness import SUBJECT, Station, build_station, headers
from .test_flow import CLIENT_TIME, _context

LOAD_SECONDS = 4.0
WRITES_PER_SECOND = 10
READ_GAP_SECONDS = 0.02
JOIN_SECONDS = 30.0
# PRD 11, "Someone needs help": "The fixed first screen visible within 500 ms ... test under load". It is the only
# latency number PRD 11 states, and E32 11 asks every PRD 11 target to hold while medicines writes run.
P95_BOUND_SECONDS = 0.5


def _paced(rate: int, stop: threading.Event, act: Callable[[int], None]) -> None:
    """Calls act(0), act(1), ... at `rate` per second for LOAD_SECONDS; bounded by its count, not by a clock."""
    start = time.monotonic()
    for n in range(int(LOAD_SECONDS * rate)):
        if stop.wait(max(0.0, start + n / rate - time.monotonic())):
            return
        act(n)


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


@pytest.mark.slow
@pytest.mark.trace("T-63", "HZ16")
def test_medicines_reads_stay_within_the_prd_bound_under_medicines_writes(tmp_path: Path) -> None:
    st: Station = build_station(tmp_path / "medicines")
    _context(st)
    statuses: list[int] = []
    stop = threading.Event()

    def medicines_write(n: int) -> None:
        if n % 2:
            path = f"/v1/people/{SUBJECT}/medication-context"
            body: dict[str, object] = {"part": "allergies", "state": "none_known", "client_time": CLIENT_TIME}
        else:
            path = "/v1/medicines/inspections"
            body = {"lot_id": "lot-apap-001", "finding": "no_change", "client_time": CLIENT_TIME}
        response = st.client.post(
            path, json=body, headers=headers(key=f"key-load-{n:06d}", **{"X-Correlation-Id": f"cor_w{n}"})
        )
        statuses.append(response.status_code)

    writers = [threading.Thread(target=_paced, args=(WRITES_PER_SECOND, stop, medicines_write))]
    reads: list[float] = []
    reads_bound = int(LOAD_SECONDS / READ_GAP_SECONDS)
    paths = ("/v1/medicines/lots", f"/v1/people/{SUBJECT}/medication-context")
    try:
        for writer in writers:
            writer.start()
        for n in range(reads_bound):
            if not any(writer.is_alive() for writer in writers):
                break
            began = time.perf_counter()
            response = st.client.get(paths[n % 2], headers=headers(**{"X-Correlation-Id": f"cor_r{n}"}))
            reads.append(time.perf_counter() - began)
            assert response.status_code == 200, response.text
            stop.wait(READ_GAP_SECONDS)
    finally:
        stop.set()
        for writer in writers:
            writer.join(JOIN_SECONDS)
    assert not any(writer.is_alive() for writer in writers)
    assert statuses == [201] * int(LOAD_SECONDS * WRITES_PER_SECOND)
    assert len(reads) >= 50
    assert _p95(reads) <= P95_BOUND_SECONDS, f"p95 {_p95(reads):.3f} s over {len(reads)} reads"
