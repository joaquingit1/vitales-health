# E18 system scenarios

`test_real_stack.py` runs under the hub project (`uv run --project hub pytest -c hub/pyproject.toml -q
eval/scenarios/E18`) with the real composition root, real SQLite in a temporary directory, kernel clocks and IDs and
HTTP through ASGI. It installs no fake security or hub providers.

- The unconfigured hub reports degraded health and rejects an unauthenticated observation write.
- The product suites run only when the composed hub mounts every operation E18's driver calls (design 6.1, listed in
  `eval/bench/src/crew_health_bench/domain/readiness.py`). The test reads the operations the real composition mounts
  and checks that E18's readiness rule names exactly the missing ones. On main on 2026-11-15, 13 of the 17 are missing
  (among them `createSession`, `startEpisode`, `startAssessment` and `getEvidencePassage`), so the suites are
  `not_run(provider_missing)`; when the providers land the same test requires them to be ready.

V33, V34 and R46 end to end acceptance still needs those providers and E21's harness (`eval/runner`, PR 38).
E18 owns no UI or D scenario.
