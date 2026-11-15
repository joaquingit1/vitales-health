# Pieces supplied by other epics

| Path | What it is | Design section | Supplied by |
|---|---|---|---|
| `hub/src/crew_health/kernel/artifacts.py` | Immutable content-addressed artifact/reference/receipt types and structural publication, pin and backup-reference barrier ports. The protected platform implementation remains owner work; a publication receipt never acknowledges a feature command. | SYSTEM 5.7/7.2; E10 5; R-E10-16 | E10 T1 |
| `hub/tests/kernel/test_artifacts.py` | Property and invalid-input tests for the shared artifact identity types. No filesystem durability claim. | SYSTEM 5.7; E10 I6 | E10 T1 |
