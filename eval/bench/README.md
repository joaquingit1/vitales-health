# E18 evaluation tooling

E18 is incomplete. No clinical result, HealthBench score, holdout run or promotion evidence has been produced.
The crew hub never imports this package. E18 owns no user interface.

The package provides grouping and sampling, fingerprints and file leak checks, deterministic reveal-table simulation,
rubric mechanisms, metrics, comparisons, claims checks, release gates, configuration validation and byte freezing.
The `Case.visible` string remains a grouping projection rather than a complete authored case/seed document.

`FileArtifactStore` implements the E18-owned `ArtifactStore` port. It uses a process lock, bounded hash-chained JSONL,
fsync, torn-tail recovery and atomic replacement. The hash covers sequence, record and predecessor digest. Both
`max_line_bytes` and the frozen record count must be supplied by composition. Storage faults escape as typed failures.
The current writer has POSIX directory barriers. It refuses Windows before creating the root because a verified
native directory persistence barrier is missing. Read-only tooling remains available; native Windows writes and
power-loss acceptance are unfinished, not silently accepted without a barrier.

`RunLifecycle` implements planning, generation, grading, invalidation and report publication over injected ports.
Inputs are verified before generation and grading; drift aborts. Resume uses the frozen case/repeat order and only
skips committed records. Grading checks the committed generation digest and a distinct process epoch. The report
manifest is written last, and `reported` is recorded after all artifacts. This library is not a complete runner
composition, model-exclusion mechanism or operating-system sandbox.

`publishable_artifacts` builds schema-validated, labeled run manifests, case rows and category aggregates. It retains
the full input manifest, salts case references, removes private trace/evidence text and scans for supplied secrets.
Unrun cases remain in the denominator with null durations. Missing grades stay missing. A caller must supply the
actual case plan, complete secret/canary inventory and check/category mapping; no production composition is supplied.

`AgeVault` uses the design's pyrage dependency for actual age encryption. Sources must be outside the repository;
only ciphertext and hash receipts are written to the vault. Repeated sealing verifies prior bytes. Grader decryption
requires an identity and distinct injected generation/grader epochs, uses a temporary plaintext directory and cleans
it in `finally`. This epoch guard does not establish process isolation or independent-owner authorization by itself.
The current vault reader returns parsed JSON, not a complete validated HiddenPart/rubric document.

Implemented CLI commands are `lint claims`, file `leak scan`, `vault seal` and `vault verify`.
Vault commands read recipients from `partition-policy.json` and never read a private identity. Empty recipients refuse
sealing. The remaining commands in E18 section 6.2, directory scanning, full case schemas, partition publication,
preregistration, the official harness adapter and the experiment runner remain unfinished.

`config/healthbench-plan.json` records inspected official source and measured dataset hashes. See
`HEALTHBENCH-SOURCE.md` for source URLs and integration findings. Source/dataset inspection happened during external
preparation, not in product code or tests. No dataset was retained. The plan is not evidence of a completed run.

Run the exact commands in `docs/build/E18/T1.gates`. Real-stack scenarios instantiate the actual composition root and
SQLite. They prove unconfigured refusal and deliberately expose absent provider operations. They do not substitute
passing unit evidence for V33, V34 or R46. Protected provider files are unchanged.

Development rules:

- E18 owns the shared ArtifactStore implementation in design sections 4.4 and 5.1. Reuse it rather than creating
  another transaction, lock or recovery algorithm. The user brief authorizes building an unanswered shared seam.
- Preserve evidence kind with its identity, and distinguish a selected file from the complete selected inventory.
- Validate cross-platform paths before mutation, including ciphertext receipts and temporary paths.
- Validate frozen case order and generation commit identity before a resumed command returns success.
- A serialized public artifact must preserve every input pin and every planned case, including unmeasured durations.

- Persist a completed tool process's exit status before clearing its session token. Store absent tokens as null;
  an undefined value loses the completion evidence and forces an unnecessary repeat.
