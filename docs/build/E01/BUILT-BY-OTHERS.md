# Pieces supplied by other epics

| Path | What it is | Design section | Supplied by |
|---|---|---|---|
| `hub/src/crew_health/kernel/process_clock.py` | Public native UTC and monotonic `Clock` adapter for development evaluation processes. Epoch is injected, validated and fixed for the lifetime of the adapter. | SYSTEM section 5.2; E01 public Clock port | E18 T1 |
| `hub/tests/kernel/test_process_clock.py` | Native Clock contract test. Does not test clinical timers or read devices. | SYSTEM section 5.2 | E18 T1 |

- `hub/src/crew_health/kernel/ids.py`: pure KernelIdFactory with injected UUID hex source, canonical derived IDs and typed validation; SYSTEM 5.1, E18 T1. E18 cannot import platform SystemIds across its boundary.
- `hub/tests/kernel/test_id_factory.py`: property and invalid-ID conformance tests for that shared provider; SYSTEM 5.1, E18 T1. Protected platform SystemIds remains unchanged.
