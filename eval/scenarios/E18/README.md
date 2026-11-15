# E18 system scenarios

`test_real_stack.py` uses the real hub composition root, real SQLite in a temporary directory, kernel clocks/IDs and
HTTP through ASGI. It installs no fake security or hub providers. The refusal scenario verifies degraded health and
unauthenticated observation rejection.

Three failing prerequisite cases expose absent `getEvidencePassage`, `startAssessment` and `startEpisode` operations
in that composition. They keep V33, V34 and R46 acceptance visibly open. They are not the complete acceptance
scenarios: rubric-tool isolation, an unsupplied-lab turn, recorder evidence and paired rules/assistant cases still
need their real providers and driver.

The scenario gate uses the hub's pinned environment for its composition dependencies. `--confcutdir` selects this
standalone scenario directory; these tests use no E21 parent fixtures. No provider test or assertion is deselected.
The separate full E18 component gate uses the bench environment. E18 owns no UI or D scenario.
