# Pieces supplied by other epics

| Path | What it is | Design section | Supplied by |
|---|---|---|---|
| `hub/src/crew_health/kernel/ids.py` | Pure `KernelIdFactory` with an injected UUID hex source, canonical derived IDs and typed validation. E18 cannot import platform `SystemIds` across its boundary. | SYSTEM section 5.1 | E18 T1 |
| `hub/tests/kernel/test_id_factory.py` | Property and invalid ID conformance tests for that provider. Platform `SystemIds` is unchanged. | SYSTEM section 5.1 | E18 T1 |
