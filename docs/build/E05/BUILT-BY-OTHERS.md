# E05 files changed by other epics

Edits to E05-owned files made by another epic under AGENTS.md section 1 rule 9, because that epic needed them
before E05 could. Each is the smallest change; E05 may replace it.

| Path | What changed | Why | Built by |
|---|---|---|---|
| hub/src/crew_health/measurement/application/ports.py | `OriginMode`, `OperatorRequirement`, `GapReason`, `CapabilityStatus` and `UnavailableReason` are defined in the ports file as copies of `measurement.domain.model`'s aliases instead of being imported from the domain package | Consumers import only an owner's ports file (SYSTEM 6.4); E12's import contract failed on the domain import (review r1 finding 9). `hub/tests/tools/test_owner_aliases.py` pins each copy equal to the domain definition | E12 F1 |
