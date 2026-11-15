# E18 evaluation tooling (`crew_health_bench`)

Offline evaluation for Crew Health Check: HealthBench on the pinned official harness and the product suites. The crew
hub never imports this package and it never runs on a crew hub. It owns no user interface.

No HealthBench score, clinical result, holdout run or gate verdict exists yet. Every result needs inputs only the
evaluation owner can supply (below).

## What runs today

- HealthBench, model-only track: `chc-bench healthbench fetch | fingerprints | preregister | plan | generate | grade |
  report`. Generation runs in a separate worker process with the grader key and vault identity stripped; grading
  runs in a later process with the pinned simple-evals scorer (`HEALTHBENCH-SOURCE.md`). Without the grader
  credential `grade` refuses with `grader_credential_missing` before recording anything. `tests/test_healthbench_cli.py`
  drives every command on a synthetic dataset; `tests/run_real_sample.py` drives them on the real files.
- HealthBench, system-assisted track: until E13 provides `BenchmarkAnswerPort`, every case is `not_run` with
  `no_evaluation_entry_point`.
- Case format, validation, partition freeze and verify, fingerprints and canaries, the age vault, the leak scanner,
  claims lint, typed rubric checks, metrics, the release gate and the freeze library, as libraries and the
  `cases`, `partitions`, `vault`, `leak` and `lint` commands.
- Product suites: `SuiteGenerator` records `not_run` with `harness_unavailable` (E21's `eval/runner` is not on main) or
  `provider_missing` and the missing hub operations (`domain/readiness.py`). The hub scenario
  `eval/scenarios/E18/test_real_stack.py` checks that rule against the real composition.
- Run storage: `FileArtifactStore`, hash-chained, fsynced JSONL with torn-tail recovery and atomic files. On native
  Windows new streams and atomic files commit through `MoveFileExW` with write-through, appends flush with
  `FlushFileBuffers`, and a write-through marker move is the directory barrier; torn-tail recovery is the same code
  on every platform. It refuses (exit 6) only when that kernel call cannot load. The Windows branch is tested through
  an injected kernel move; it has not run on a Windows machine. Design section 9 places E18 on R1 or a ground node, and
  names R2 (the Windows laptop) only for turn time when the model fits.

## What a real run needs from the owner

1. Q-E18-1: a grader API key with a spending limit, exported as `OPENAI_API_KEY` in the grading shell only.
2. Q-E18-2: the evaluation owner. Their age public key goes in `config/partition-policy.json` `vault.recipients`; their
   identity file path in `CHC_EVAL_IDENTITY` for `preregister`, `report` and holdout grading; a 32-byte case salt
   kept outside the repository (or `eval/vault/salt.age`). They author and adjudicate the holdout labels; E18 invents
   none.
3. An E17 model release file (`model/releases/<release_id>.json`, mode `live`) and its `llama-server` on loopback.
4. The local datasets: `healthbench fetch` (see `HEALTHBENCH-SOURCE.md`).

Then, from the repository root (`--corpus` and `--root` stay outside git):

```
B="uv run --frozen --project eval/bench chc-bench --config-dir eval/bench/config healthbench"
C="--root eval/bench/runs --corpus eval/vault/healthbench --model-release model/releases/<release_id>.json"
$B preregister $C --base-url http://127.0.0.1:<port>          # owner identity; times warm-up, freezes n per variant
$B plan $C --manifest <plan input>                              # suite, track, variant, case_set_sha256 of the sample
$B generate $C --run-id <run_id> --base-url http://127.0.0.1:<port>
OPENAI_API_KEY=... $B grade $C --run-id <run_id> --grader-url https://api.openai.com
$B report $C --run-id <run_id> --grader-url https://api.openai.com --salt <salt> --published eval/bench/results
```

Main runs first; Hard and Consensus are separate runs. The plan input holds `suite`, `track`, `partition`, `variant`,
`case_set_sha256` (of the preregistered `case_ids`), `prompt_sha256`, `tool_catalog_sha256`, `evidence_release_id`,
`evidence_sha256`, `registry_version`, `config_release_id`, `seed`, `repeats`, `hardware` and `software`; `plan` measures
and adds the release, decoding, harness, grader, integrity, dataset, preregistration and document hashes.

## Gates

`docs/build/E18/T1.gates`. Run the bench tests from inside the project (`uv run --frozen --project eval/bench ...`);
the hub scenario runs under the hub project.
