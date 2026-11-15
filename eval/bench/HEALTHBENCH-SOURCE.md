# HealthBench source and local inputs

Harness: https://github.com/openai/simple-evals at commit `652c89d0ca9df547706735883097e9537d40dc47` (catalog L02,
`sources/P03-healthbench.txt`). `vendor/` keeps the upstream bytes of `healthbench_eval.py`, `common.py`, `types.py`,
`sampler/chat_completion_sampler.py` and the MIT license, their SHA-256 values in `vendor/SOURCE.json`, and a wheel
built from those bytes without edits (`vendor/build_wheel.py`). Upstream has no packaging metadata at that commit,
so E18 design 5.2's direct Git dependency cannot install; the wheel is the packaging boundary. Every grading command
checks the installed module's SHA-256 against `config/healthbench-plan.json` before it grades.

## Datasets

The pinned module names three files. Their URLs, byte counts, example counts and SHA-256 values are in
`config/healthbench-plan.json`:

| Variant | File | Examples | Bytes | SHA-256 |
|---|---|---|---|---|
| main | `2025-05-07-06-14-12_oss_eval.jsonl` | 5000 | 60258154 | `e99dd3c6...ecd2f` |
| Hard | `hard_2025-05-08-21-00-10.jsonl` | 1000 | 12581564 | `b0320430...74e9` |
| Consensus | `consensus_2025-05-09-20-00-46.jsonl` | 3671 | 36921504 | `fabe3755...69d6` |

Every Hard and Consensus `prompt_id` is also a main `prompt_id` (measured on 2026-11-15: main adds 5000 IDs, Hard and
Consensus add none). The variants are still separate runs and never pooled (VALIDATION section 5).

The files are the hidden evaluation input. They live only in the ignored `eval/vault/healthbench/` (about 110 MB, plus
the prepared copies) and are never committed. To get them:

```
uv run --frozen --project eval/bench chc-bench --config-dir eval/bench/config \
  healthbench fetch --root eval/vault/healthbench --variant main     # then Hard, then Consensus
```

Without a file argument `fetch` streams the pinned URL, refuses any byte past the pinned size, checks the SHA-256 and
only then prepares `<variant>.jsonl`, the generation-side projection `<variant>.visible.jsonl` (prompt ID and
messages only) and a receipt. With a file argument it verifies and prepares a local copy. On 2026-11-15 the three
files were fetched from the URLs above and all three hashes matched the plan.

## Fingerprints

`healthbench fingerprints --root eval/vault/healthbench --corpus eval/vault/healthbench` writes
`eval/bench/fingerprints/healthbench.fp.json`: for each example, salted 8-gram hashes of the prompt messages, rubric
lines and ideal completions, with the public `fp_salt` of `partition-policy.json`, plus the HealthBench canary. A hash
is kept when its value is a multiple of `fingerprint_sample_modulus` (16): the full set is about 2.3 million shingles
(over 40 MB as JSON), while containment measured against a target's full shingle set stays an unbiased estimate with the
sample. Each text (prompt message, rubric line, ideal completion) keeps its own list in `parts` and is scanned on its
own as `<case_ref>:<index>`, so one copied rubric line is never diluted by the rest of its example; a text whose sample
holds fewer than `fingerprint_min_shingles` (3) hashes keeps its 3 lowest instead (78,709 texts, 7.6 MB, under the
8 MiB canonical JSON ceiling). Case references are salted; no text is written. The leak scan gate checks
`model/` and `config/` against it.

## Grader

The grader is the pinned module's normal grader, `gpt-4.1-2025-04-14`, called with the module's template and the
sampler values recorded in the plan. Its key comes only from the environment variable named by
`grader.api_key_env` (`OPENAI_API_KEY`) in the grading process; generation strips it. Without it, `healthbench grade`
refuses with `grader_credential_missing` (exit 6) before recording anything (Q-E18-1). Upstream retries a malformed
grade without a bound (`healthbench_eval.py:415`); E18 wraps the grader in a sampler bounded to
`rubric items x (1 + bench.grader.max_retries)` calls. An exhausted grader leaves the example ungraded, never zero.

## What exists and what does not

No HealthBench score exists. No generation of any real model and no grader call has run. The plumbing was exercised
end to end on the real files with loopback stand-ins (`tests/run_real_sample.py`); that run produces no number about
any model and its outputs are deleted.
