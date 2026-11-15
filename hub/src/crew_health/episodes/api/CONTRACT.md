# Episode API extension

The frozen `contracts/api` package is unchanged. `getEpisode` with ordinary `application/json` returns exactly the original Episode fields and original followup task projection. A caller that sends `Accept: application/vnd.crew-health.episode+json` receives `schemas/episode-details.schema.json`, including attributed history, task revisions, withheld-answer counts, due state and overdue notices. This is an explicit opt-in extension of E15 design requests R-E15-1 to R-E15-6, not an edit to the immutable package or an assertion that its coordinator requests were approved.

Routes registered by `episodes.module.register`:

| Method | Path | operationId | Input | Output |
|---|---|---|---|---|
| GET | `/v1/episodes/{episode_id}` | getEpisode | authenticated session | frozen Episode or opt-in EpisodeDetails |
| POST | `/v1/episodes/{episode_id}/reviews` | recordReview | `review-create.schema.json`, Idempotency-Key, If-Match | ClinicalReview with E15 authority/revision metadata |
| POST | `/v1/episodes/{episode_id}/actions` | actOnEpisode | `episode-action.schema.json`, Idempotency-Key, If-Match | frozen Episode or opt-in EpisodeDetails |
| GET | `/v1/episodes/{episode_id}/timeline` | getEpisodeTimeline | cursor, limit (1 to 200) | episode_id, revision, items, next_cursor |
| GET | `/v1/episodes` | listEpisodes | patient_id, repeated state, cursor, limit | items, page.next_cursor, page.has_more |
| GET | `/v1/episodes/{episode_id}/reviews` | listReviews | cursor, limit | items, next_cursor |

Identity, origin, consent, roster, linked-record readers and pinned rules are host-injected providers. An absent or failed clinical reader gives a retryable error instead of fabricated content. No route creates an episode: the E04 startEpisode handler owns intake and calls EpisodeKernel.create in its transaction. Commands accept human actors only; ground may record advice but cannot change tasks or resolve an episode. Signed user text remains private clinical state, never an audit or replication payload.

`episode-rules.json` is E15-owned sourced configuration. Its review cadences remain pending clinical review; chosen custom-task bounds are labeled team assumptions. E01 currently has no ConfigName for this document and no module audit-policy registration hook. Until E01 supplies these integrations, the host must explicitly construct EpisodeRulesSource from validated immutable bytes and install the module migration and reference-only audit policies before registration. The standalone tests demonstrate this composition with real SQLite and CommandLedger. They do not prove E01's normal serve command discovers this feature.
