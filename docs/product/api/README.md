# Crew Health Check hub API

This folder is the machine readable form of [CONTRACTS.md](../CONTRACTS.md). CONTRACTS.md is the normative text: when this folder and CONTRACTS.md disagree, CONTRACTS.md wins and the folder is wrong. A backend developer builds the hub from CONTRACTS.md (sections 11 to 27 hold the engines, section 20 the build order and section 28 the Earth-independence invariants) and checks every request and response against the files here.

## What is here

| Path | Contents |
|---|---|
| `openapi.yaml` | OpenAPI 3.1: 121 paths, 133 operations, every request body, response, header, role and error code |
| `schemas/` | 101 JSON Schema 2020-12 files. Each `$id` is its file name and references are relative |
| `schemas/common.schema.json` | The fixed vocabulary (environments, overlays, levels, raisers, cadence words, body regions, origin modes, basis, gap kinds, plan stages and modes, who) and shared types |
| `schemas/error.schema.json` | The error envelope and every error code |
| `schemas/config-*.schema.json` | One schema per configuration document (18), plus the document, index and release wrappers (CONTRACTS section 11) |
| `schemas/requests.schema.json` | Every request body, as `$defs` (47) |
| `schemas/event.schema.json`, `event-page.schema.json`, `assistant-stream-chunk.schema.json` | The event stream and the assistant stream |
| The other schemas | One per object or screen view, named after it (`help-session.schema.json` is HelpSession) |
| `examples/` | 248 examples, one folder per area: `assistant`, `check`, `clinical`, `config`, `configuration`, `context`, `crew`, `drills`, `environment`, `errors`, `events`, `ground`, `help`, `kit-map`, `participation`, `plans`, `requests`, `roles`, `routine`, `sensors`, `session`, `supplies`, `views`, `warnings` |
| `examples/config/` | An example of each team configuration document (14) |
| `examples/kit-map.example.json` | The example kit map, as a raw document with `example: true` |

## Examples are not seed data

Every example is illustrative. Response examples are marked `SIM`, `REPLAY` or `SIM and REPLAY` at the start of their `summary`; request examples say `SIM request`; configuration examples say `EXAMPLE CONFIG`. They reproduce the demo in all four environments (station, transit, Moon base, Mars base), with values from design/crew-data.js and the replay files in nasa-data/, so a backend can test against them. Do not load them into a hub as data: a deployment writes its own configuration documents, runs them through the loader (CONTRACTS section 11) and records its own readings and answers. The example kit map cannot be activated while the mission mode is LIVE.

## How to read it

1. Read CONTRACTS.md sections 11 (configuration and the no-hardcoded-data rule), 5 (intents, headers, errors) and 20 (modules, build order, test vectors).
2. In `openapi.yaml`, each operation has:
   - `tags` that name its area and a `description` that points to its CONTRACTS section;
   - `x-roles`, the session roles allowed to call it (CONTRACTS section 20);
   - a request body that refers to `schemas/requests.schema.json#/$defs/<Name>`;
   - responses with the object schema and named examples;
   - error responses grouped by HTTP status, each with `x-error-codes` and the envelope from `schemas/error.schema.json`;
   - the `Idempotency-Key`, `If-Match`, `If-None-Match`, `Config-Release` and `X-Correlation-Id` headers where they apply.
3. An example file is an OpenAPI Example Object plus one extension: `{ "summary", "description", "x-schema", "value" }`. `x-schema` names the schema `value` must satisfy: a schema file, a request body (`requests.schema.json#/$defs/HelpAction`), or `list:<schema>` for a list response `{ "items": [...], "page": {...} }`.
4. Some examples come in sequences. `examples/help/eye-01-opened.json` to `eye-10-closed.json` walk one help session from opening to close. `ground-message-queued.json`, `-sent.json`, `-acknowledged.json` and `-replied.json` are the message for the ground. `kit-map/view-station.json`, `view-mars.json`, `what-if-ecg.json` and `what-if-supplies-skills.json` are the kit map. `plans/run-icl10-opened.json`, `-watching.json`, `-escalated.json` and `-closed.json` walk ICL10's plan run in act 5; `plans/guided-check-teaching.json`, `-counting.json` and `-agree.json` walk Tomas Lind's guided check, with `-disagree.json` and `-no-reference.json` beside them. `plans/view-icl10-lunar.json` and `view-icl10-lunar-link-up.json` differ only in the ground stage (EI2). `help/ground-message-preview.json` is the For the ground card before the press; its `text_sha256` equals the one in `help/ground-message-queued.json`.

## How to validate

Both checks need Node.js 18 or later. jq is needed for the second.

Lint the OpenAPI document (structure, references, operation IDs, parameters):

```sh
cd api
npx -y @redocly/cli@1.25.11 lint --extends=minimal --skip-rule=no-invalid-media-type-examples openapi.yaml
```

The skipped rule validates examples after bundling, and bundling loses the schemas' relative `$id` base, so it reports unresolved references that are not there. The next check validates the examples properly.

Validate every example against the schema in its `x-schema` (the kit map example against `config-kit-map.schema.json`):

```sh
cd api
AJV="npx -y -p ajv-cli@5.0.0 -p ajv-formats@3.0.1 ajv"
tmp=$(mktemp -d); fail=0
for f in $(find examples -name '*.json' | sort); do
  if [ "$f" = examples/kit-map.example.json ]; then
    ref=config-kit-map.schema.json; cp "$f" "$tmp/value.json"
  else
    ref=$(jq -r '."x-schema"' "$f"); jq '.value' "$f" > "$tmp/value.json"
  fi
  case "$ref" in
    list:*) printf '{"$id":"check.json","type":"object","required":["items","page"],"properties":{"items":{"type":"array","items":{"$ref":"%s"}},"page":{"$ref":"common.schema.json#/$defs/page"}}}' "${ref#list:}" > "$tmp/check.json" ;;
    *) printf '{"$id":"check.json","$ref":"%s"}' "$ref" > "$tmp/check.json" ;;
  esac
  $AJV validate --spec=draft2020 --strict=false -c ajv-formats -s "$tmp/check.json" -r 'schemas/*.schema.json' -d "$tmp/value.json" >/dev/null 2>&1 || { echo "FAIL $f"; fail=1; }
done
echo "done, failures: $fail"
```

The same command validates a real configuration document: set `ref` to its schema (`config-responder-flows.schema.json` for `../responder-flows.json`) and validate the file itself instead of its `value`.

Schema validity is necessary but not enough. The hub's loader also runs the 16 cross checks of CONTRACTS section 11: copied values equal their source in environment-hazards.json, every source ID resolves, every template uses only its listed placeholders, no document has a dose field, and the rest. A backend implements those checks in its loader and runs them on every release.

## How to mock

Run a mock of the whole API with [Prism](https://stoplight.io/open-source/prism) so client work can start before the hub exists. The second server entry in `openapi.yaml` points at it.

```sh
npx -y @stoplight/prism-cli@5.12.0 mock -p 4010 api/openapi.yaml
```

- Every response comes from the named examples. Pick one with `Prefer: example=<key>`, where the key is the example's path under `examples/` without `.json`, with every character that is not a letter or digit replaced by `_`. For example `Prefer: example=help_eye_05_eye_flow` or `Prefer: example=environment_ground_link_down`.
- Ask for an error with `Prefer: code=<status>`, for example `Prefer: code=409`.
- Prism validates requests against the schemas: send `Authorization: Bearer <anything>`, an `Idempotency-Key` of 8 to 128 characters on mutating calls and `If-Match` where the operation requires it, or it answers with an error.
- The mock keeps no state. An action returns its example, not the next state; walk a sequence with `Prefer` (for example `help_eye_01_opened` to `help_eye_10_closed`). Behavior such as the help state machine, the rule engine and the kit gate is specified in CONTRACTS.md and exists only in the hub.
- `POST /v1/demo/ground-link` is a demo only route. The mock serves it; the hub serves it only in demo mode.
