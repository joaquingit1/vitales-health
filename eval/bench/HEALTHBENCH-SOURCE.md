# HealthBench source inspection

Preparation inspected the official source at
https://github.com/openai/simple-evals/tree/652c89d0ca9df547706735883097e9537d40dc47.
`config/healthbench-plan.json` records the measured module digest and dataset hashes. Dataset bytes were streamed
through SHA-256 during preparation, outside product code and tests, and discarded. No example or answer is committed.
These measurements are provenance, not a benchmark result or clinical review.

The inspected adapter surface is `HealthBenchEval(grader_model, ..., n_threads, input_path)`;
`grade_sample(prompt, response_text, example_tags, rubric_items)` returns metrics, private explanation and private
rubric rows. `calculate_score` and `_aggregate_get_clipped_mean` are the actual upstream scoring and aggregation
functions. The normal grader in `simple_evals.py:333` is `gpt-4.1-2025-04-14`. The grader template hash was measured
from the literal AST without importing or executing the module.

Design corrections requiring review:

- The source repository has no `pyproject.toml` or package initializer at its root. E18 design 5.2's direct Git
  dependency cannot be installed as specified. A source packaging boundary must preserve upstream bytes and license.
- `healthbench_eval.py:415` retries malformed grades without a bound. Its sampler also retries without a bound.
  E18 must supply a bounded sampler and a process deadline while leaving scoring unchanged. An exhausted grader
  remains blocked and ungraded; no replacement score is allowed.
- Main is 60,258,154 bytes and Consensus is 36,921,504 bytes. E18's 16 MiBy leak-file limit cannot load them whole.
  Preparation and fingerprinting need bounded JSONL streaming, not a larger safety tolerance.

No official scorer adapter, vendor bundle, grader call, generation, preregistration or measured score is claimed here.
The missing grader credential and independent holdout owner remain external inputs. Dataset/network preparation never
runs in tests. Annex integration is left for the package owner because this task cannot edit `docs/product/`.
