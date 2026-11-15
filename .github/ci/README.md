# CI execution and coverage policy

`CI` runs on every pull request, every push to main and manual dispatch. There are no changed-path filters.
The reusable lint workflow runs Python and crew quality checks on Ubuntu, macOS and Windows. The final `complete`
job requires every quality job, test shard and per-OS coverage job to succeed, including after cancellation.

A complete Ubuntu pytest collection of `hub/tests` and `eval/scenarios` creates one manifest. Every test belongs to
exactly one of six file groups: four balanced core groups, contracts and installed-wheel checks. Each group runs on
all three operating systems. Before execution each worker checks its actual collected identities against that exact
manifest. New tests enter automatically. File arguments avoid Windows command-line limits from thousands of node IDs.
The contracts group alone needs pnpm for its generation checks; generation assertions remain in full on every OS.

The initial timings come from Ubuntu JUnit in run 43730835232 at b036a47. Pytest took 517.74s on Ubuntu and 389.96s on
macOS. The Ubuntu case durations total 497.538s; the largest single plans-policy case took 33.475s, installed wheel 7.807s.
These are measurements, not test deadlines or guarantees of future speed. `test-timings.json` balances files by the slowest measured per-file
case sum across Ubuntu and the completed Windows run (1580.953s total); new files use their collected case count as a scheduling weight. Each run preserves verbose logs,
JUnit, slowest-case durations and collection proof. Evidence directories exist before pytest begins.

## D31, explicit owner decision on 2026-11-15

Joaquin chose "Enable CI with a coverage ratchet (Recommended)" after the actual legacy branch figure was measured.
The legacy target remains 95%; new files and files acquiring their first branches must reach 95%. Existing files must
not decrease their exact covered/total branch fraction. Improvements in one file cannot compensate for another file.
Fractions use integer cross multiplication, without percentage rounding or a tolerance. The baseline includes E01;
D27 remains recorded and is not used to exclude its source. This policy changes legacy coverage blocking, not tests,
safety checks or portability requirements.

`branch-baseline.json` records the measured Ubuntu XML provenance, its digest, source digests and each file's branch
counts. macOS had the same 5119/6014 aggregate branches. The completed Windows run 43734316264 also measured 5119/6014, with no file below that floor. Its six test failures
are separate blockers; matching coverage does not make that test run successful. Windows must meet the same floor. Baseline changes need explicit review and measured
provenance. Never regenerate it automatically from a failing run. The job fetches the trusted PR base commit or
pre-push main commit, and rejects a lower proposed baseline or removal of a still-present file. New baseline entries
require 95%, and files absent from that trusted source inventory independently require 95%, even when the PR adds
them to its baseline. The first introduction is bound to the exact approved measured snapshot digest in the checker.

Each OS combines all six raw coverage databases using coverage.py's arc union. Combining XML totals or combining
across operating systems would mask gaps and is not allowed. Every fragment must carry the current source identity,
manifest digest and exact collection proof. Missing or duplicate fragments fail. The reporter checks every tracked
Python source under domain/application, including unmeasured and new files. A missing baseline file is allowed only
when the source is actually deleted from the tracked inventory. It cannot disappear from measurement while present.

Local harness tests include a fraction property and a real six-fragment arc-combine fixture:

```
uv run --project hub --locked pytest -c hub/pyproject.toml -q hub/tests/ci
```

The full OS suites run remotely. Local harness success does not establish Windows or full-suite success.

## Windows corrections from the completed run

The UTF-8 write regression is reproduced under a forced cp1252 default, then repaired explicitly. Other test text
reads and writes now name UTF-8. No performance assertion or execution deadline is increased.

The old closed-port fixture assumed every OS refuses a connection within 100ms. That assumption is invalid: Python
permits network-stack timeouts, and Windows can suppress resets for ports without listeners. The runner's exact
stealth-mode setting is unknown. The real delayed-server 100ms deadline test remains; an explicit connection refusal
separately verifies its exact typed reason. The proxy test now succeeds against a real worker and checks its exact
bearer/body plus an empty proxy capture, strengthening its previous unreachable-only assertion.

The optional worker scenario starts the resolved project Python interpreter directly. Terminating a Windows uv
launcher had left its Python worker alive. The scenario now checks both owned PID termination and a closed port,
then observes and rethrows the same real request's transport exception to assert its exact timeout/refusal reason.
Its deterministic baseline must remain unchanged. There is no second reason probe or accepted-reason fallback.
