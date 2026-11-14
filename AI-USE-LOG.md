# AI-use log

One row per AI run that produced work (KICKOFF.md section 8). The product itself runs a local medical language model
(HuatuoGPT-3-9B) behind a typed tool gateway; nothing safety related depends on it.

"Coordinator" is the Claude Code session (Claude Opus 5.5) that plans, briefs, reviews and greenlights. Every row
marked "checked by coordinator" was accepted only after its gate commands passed.

| Time | Who | Tool and model | What for | What it produced | Where it landed | Checked by |
|---|---|---|---|---|---|---|
| Sat 09:00 | Coordinator | Claude Code, Claude Opus 5.5 | Root cause the potency engine's failed independent validation | Coordinator decisions, spec changes | docs/requests/COORDINATOR-DECISIONS.md, docs/ENGINE-SPEC.md | Team |
| Sat 09:31 | Builder CX-A | Codex CLI, GPT-6 Astra | Engine foundation round (types, settings, anchors) | Code and tests | pharmacy/, tests/ | Coordinator (gates, review) |
| Sat 10:05 | Builder CX-A2 | Codex CLI, GPT-6 Astra | Foundation follow-up | Code and tests | pharmacy/, tests/ | Coordinator (gates, review) |
| Sat 10:10 | Builders CX-B, CX-C, CX-CAL, CX-D | Codex CLI, GPT-6 Astra | Engine round 2: compression, chemistry, calibration, validation | Code, tests, calibration data | pharmacy/, tests/, data/pharmacy/calibration/ | Coordinator (gates, review) |
| Sat 10:40 | Coordinator | Claude Code, Claude Opus 5.5 | Calibration fixes (mean location, rate weighted internal humidity derivative) | Code, tests, regenerated calibration | pharmacy/calibration.py, tests/unit/calibration/, config/pharmacy/ | Coordinator (gates) |
| Sat 10:50 | Research agents | Claude Code subagents, Claude Opus 5.5 | Deployment research: vehicle computing, surface and comms, medical systems and standards | Research notes with sources | build folder research/ (local) | Coordinator |
| Sat 11:00 | Coordinator | Claude Code, Claude Opus 5.5 | Build charter and design template | Charter, template | docs/BUILD-CHARTER.md, docs/tdd/TEMPLATE.md | Team |
| Sat 11:10 | Design authors | Claude Code workflow, Claude Opus 5.5 | Technical designs for 21 epics | Design documents | docs/tdd/E*.md | GPT-6 Astra review, coordinator |
