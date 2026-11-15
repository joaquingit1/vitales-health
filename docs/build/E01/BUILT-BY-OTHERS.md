# E01 files changed by other epics

Edits to E01-owned files made by another epic under AGENTS.md section 1 rule 9, because that epic needed them
before E01 could. Each is the smallest change; E01 may replace it.

| Path | What changed | Why | Built by |
|---|---|---|---|
| hub/src/crew_health/config/application/ports.py | `ValidationResult` is a structural Protocol (errors, warnings, executed) in the ports file instead of a `TYPE_CHECKING` import of `config.application.loader` | Consumers import only the ports file (SYSTEM 6.4); the loader import reached `config.domain` and broke E12's import contract (review r1 finding 9). `hub/tests/tools/test_owner_aliases.py` checks the loader's result still carries those fields | E12 F1 |
# Pieces supplied by other epics

| Path | What it is | Design section | Supplied by |
|---|---|---|---|
| `hub/src/crew_health/kernel/ids.py` | Pure `KernelIdFactory` with an injected UUID hex source, canonical derived IDs and typed validation. E18 cannot import platform `SystemIds` across its boundary. | SYSTEM section 5.1 | E18 T1 |
| `hub/tests/kernel/test_id_factory.py` | Property and invalid ID conformance tests for that provider. Platform `SystemIds` is unchanged. | SYSTEM section 5.1 | E18 T1 |
