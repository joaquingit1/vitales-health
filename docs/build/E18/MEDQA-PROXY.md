# MedQA proxy run: model validation of a general medical knowledge proxy

This is model validation of a general medical knowledge proxy. It is not clinical validation, not HealthBench, and it
changes none of the AGENTS section 7 statuses: no clinician judged any output, and a licensing exam multiple-choice
score says nothing about safety or usefulness for a crew.

## Why this benchmark

HealthBench needs a paid gpt-4.1 grader (Q-E18-1), which we do not have. MedQA USMLE 4-option (Jin et al. 2020,
arXiv:2009.13081) scores by exact match of one option letter, so it needs no grader model and runs offline. The
`GBaker/MedQA-USMLE-4-options` copy is a single JSON Lines file at a pinned Hugging Face revision with a stated
license (cc-by-4.0), so the fetch is reproducible byte for byte. MedMCQA (parquet) and PubMedQA (yes/no/maybe, 3
classes) were the alternatives; MedQA is the most cited of the three for 4-option licensing exam knowledge.

## Pins

| What | Value |
| --- | --- |
| Dataset | `GBaker/MedQA-USMLE-4-options` revision `0fb93dd23a7339b6dcd27e241cb9b5eca62d4d18`, file `phrases_no_exclude_test.jsonl` (1273 items) |
| Dataset SHA-256 | `c3b905ccfa66152dc25afbcb2c10e86c0bdb208824f0658fcdb2c040f60a2beb` (2075679 bytes) |
| License | cc-by-4.0 (README front matter, README SHA-256 `c4f44b81...dde7`) |
| Weights | `huatuo-q4-first.gguf`, SHA-256 `0361c77aedf832c7a58ec32bda68de25e2d437841e734e8de0fd43c6f3a8d947`: HuatuoGPT-3-9B at `6870b60e`, Q4_K_M, equal to the E17 pin (same-host-repeatability and protocol-smoke reports on `epic/E17-wave2`) |
| Runtime | llama.cpp `llama-server` 0.7.0, build 11669, commit `620d2225c`, binary SHA-256 `9dc31a8a...384a`, equal to E17 `model/pins/llama-cpp.json` |
| Chat template | E17 `model/templates/a4aee8af...f715.jinja`, SHA-256 checked before launch |

The command refuses to start if the binary, weights or template hash differs from `eval/bench/config/medqa-proxy.json`.

## Method (preregistered)

- Sample: seeded permutation (seed 20261115) of all 1273 item IDs; the sample is its first n, the 5 warm-up items its
  tail, so they never overlap. The 5-item warm-up probe ran before n was chosen (about 1.7 s per item), then
  n = 400 was preregistered (`docs/build/E18/medqa-proxy-preregistration.json`, sample SHA-256
  `76ce2890...0466`) before any sample item was generated. `run` recomputes the sample from the seed and refuses a
  preregistration that differs, and refuses to overwrite earlier results.
- Generation: greedy (temperature 0, top_k 1, seed 0), thinking disabled through the template's `enable_thinking`
  switch, max 16 tokens, 90 s timeout per item, context 8192, 6 threads, all layers on Metal, f16 KV cache. The
  system prompt asks for one letter only. Thinking off and the smaller context are deviations from the E17 release
  profile (thinking on, 32768 tokens): with thinking on one step took 33 to 47 s in the E17 smokes, too slow for a
  bounded run.
- Scoring: exact match of the parsed letter. The parse rule (`medqa/domain.py` `parse_answer`) accepts a reply that
  starts with one uppercase option letter, optionally after `Answer:` or a bracket, followed by punctuation,
  whitespace or the end. A timeout, an unparseable reply and a missing row count as wrong and stay in the
  denominator. A refused connection or a server error aborts the run instead of being scored.
- Interval: Wilson 95% (the existing `domain/metrics.py` helper).

## Result

| Field | Value |
| --- | --- |
| n (preregistered) | 400 |
| Correct | 317 |
| Wrong | 83 |
| Unparseable | 0 |
| Timeout or missing | 0 |
| Accuracy | 0.7925 |
| Wilson 95% interval | 0.7501 to 0.8294 |
| Wall time | 1204.7 s (2026-11-15, Mac15,3, laptop shared with other jobs) |
| Results SHA-256 | `7748663dfb2d69ecabf0a0c467e06d0beae2110da0a45be8309199c563137cce` |
| Config SHA-256 | `5562d16a1e553f3d291c0a3c08d957f6221f82f9ea31d176f859f893877051d9` |

The full report is `docs/build/E18/medqa-proxy-report.json` and the per item rows (item ID, key, reply letter,
outcome, seconds; no question text) are `docs/build/E18/medqa-proxy-results.jsonl`. The llama-server child was
stopped by the command at the end of the run.

## Reproduce

From `eval/bench`:

```
uv run python -m crew_health_bench.medqa.cli fetch
uv run python -m crew_health_bench.medqa.cli probe --binary /opt/homebrew/bin/llama-server \
  --weights <dir>/huatuo-q4-first.gguf --template <E17 template>.jinja --port 18431
uv run python -m crew_health_bench.medqa.cli preregister --n 400
uv run python -m crew_health_bench.medqa.cli run --binary /opt/homebrew/bin/llama-server \
  --weights <dir>/huatuo-q4-first.gguf --template <E17 template>.jinja --port 18431
```

Dataset, results and report land in `eval/bench/runs/medqa-proxy/` (git-ignored). `--stand-in http://127.0.0.1:<port>`
runs against a loopback stand-in; its report is labeled `stand_in_not_a_score`.

## Limits

- One run on one machine (Mac15,3, 16 GB). Greedy decoding on the same build should repeat, but repeatability was not
  measured.
- MedQA is public and old enough to be in pretraining corpora; contamination is not ruled out.
- The parse rule is strict: a reply such as `**B**` counts as unparseable. The count of unparseable replies is
  reported so the reader can see its effect.
