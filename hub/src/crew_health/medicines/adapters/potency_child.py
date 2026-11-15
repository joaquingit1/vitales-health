"""Child process of the potency worker (E32 5.3), run by path under the pharmacy interpreter with `-I`. It imports
only the standard library and pharmacy's public pipeline (AGENTS section 10), never crew_health.

argv: mode (`assess`, `replay` or `check`), bundle directory, memory limit in bytes, sample interval in ms, deadline in s.
stdin: canonical JSON `{"inputs": ..., "recorded": ...}` (`recorded` for replay only).
stdout: the canonical InventoryAssessment JSON (assess), `{"match": true|false}` (replay) or `{"ok": true}` (check:
`load_bundle` alone, for the bundle import).
Exit status: 0 done, 2 engine error (stderr carries `{"error": <class name>}`), 3 over the memory limit."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path

ENGINE_EXIT = 2
MEMORY_EXIT = 3


def _peak_bytes() -> int:
    """Peak resident memory of this process. POSIX: getrusage (bytes on macOS, KiB on Linux). Windows: the psapi
    peak working set."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        windll = ctypes.windll  # type: ignore[attr-defined,unused-ignore]  # only on Windows
        # Typed calls: untyped, ctypes passes the pseudo handle as a 32 bit int, the call fails and the counters
        # stay zero, so the limit was never reached on Windows (CI: an over-limit child reported engine_error).
        current = windll.kernel32.GetCurrentProcess
        current.restype, current.argtypes = wintypes.HANDLE, []
        info = windll.psapi.GetProcessMemoryInfo
        info.restype = wintypes.BOOL
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not info(current(), ctypes.byref(counters), counters.cb):
            return sys.maxsize  # memory that cannot be measured counts as over the limit (fail closed)
        return int(counters.PeakWorkingSetSize)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def _check_memory(limit: int) -> None:
    """Ends the child with the memory exit when its peak is over the limit."""
    if _peak_bytes() > limit:
        sys.stderr.write(json.dumps({"error": "memory_limit"}))
        sys.stderr.flush()
        os._exit(MEMORY_EXIT)


def _watch(limit: int, sample_ms: int, deadline_s: float) -> None:
    """Samples the peak every `sample_ms` for at most the deadline (the parent kills the child at the deadline)."""
    for _ in range(math.ceil(deadline_s * 1000 / sample_ms) + 1):
        _check_memory(limit)
        time.sleep(sample_ms / 1000)


def main() -> int:
    mode, bundle_dir, limit, sample_ms, deadline_s = (
        sys.argv[1],
        Path(sys.argv[2]),
        int(sys.argv[3]),
        int(sys.argv[4]),
        float(sys.argv[5]),
    )
    if hasattr(os, "nice"):
        os.nice(10)  # before numpy loads: the worker never competes with help, warnings or record entry
    threading.Thread(target=_watch, args=(limit, sample_ms, deadline_s), daemon=True).start()
    payload = json.loads(sys.stdin.read())
    try:
        from pharmacy.config import load_bundle  # child env only

        if mode != "check":  # numpy loads here, after the nice level and the memory watch are set
            from pharmacy import pipeline  # child env only
            from pharmacy.types import to_json  # child env only

        bundle = load_bundle(bundle_dir)
        if mode == "check":
            out: object = {"ok": True}
        elif mode == "replay":
            out = {"match": bool(pipeline.replay(bundle, payload["inputs"], payload["recorded"]))}
        else:
            out = to_json(pipeline.assess_inventory(bundle, payload["inputs"]))
    except Exception as error:  # every engine failure is a failed run, named by its class
        # A run that went over its limit is memory_limit whichever thread noticed first: the watcher samples on its
        # own schedule, so an engine error or a result can otherwise win the race (seen on Windows CI).
        _check_memory(limit)
        sys.stderr.write(json.dumps({"error": type(error).__name__, "detail": str(error)[:300]}))
        return ENGINE_EXIT
    _check_memory(limit)
    sys.stdout.write(json.dumps(out, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
