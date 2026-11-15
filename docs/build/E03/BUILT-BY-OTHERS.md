# E03 files built by other epics

Files that docs/tdd/E03.md section 13 gives to E03, built by another epic under AGENTS.md section 1 rule 9 because
that epic needed them before E03 existed. Each is the smallest working version; E03 completes it. Request R-E02-28
(docs/requests/E02.md) lists what E03 is asked to adopt.

| Path | What it is | E03 design section | Built by |
|---|---|---|---|
| ui/crew/package.json | Crew app package: `dev`, `build`, `typecheck` (vue-tsc), `lint` (eslint, zero warnings), `test` (Vitest) and `e2e` (Playwright) scripts; Vue, Vue Router and `@crew-health/contracts` | 7.1, 12 | E02 T1 |
| ui/crew/tsconfig.json | Strict TypeScript configuration for the app, its tests and its build configuration | 7.1 | E02 T1 |
| ui/crew/vite.config.ts | Vite with the Vue plugin and a `/v1` proxy to `CREW_HUB_ORIGIN` (127.0.0.1:8080 when unset) | 7.1, 7.3 | E02 T1 |
| ui/crew/vitest.config.ts | Vitest in jsdom over `src/**/*.test.ts` and `src/tests/**/*.spec.ts` | 12 | E02 T1 |
| ui/crew/playwright.config.ts | Playwright on Chromium with the back-forward cache allowed, over `src/features/**/*.spec.ts`; starts the E02 test station hub (hub/tests/privacy/e02_station.py) on port 8765 and Vite on port 5175 | 12 | E02 T1 |
| ui/crew/eslint.config.js | eslint with the strict TypeScript and Vue rules; Node globals refused in app code | 7.1 | E02 T1 |
| ui/crew/index.html | The page that mounts the app | 7.1 | E02 T1 |
| ui/crew/src/main.ts | Composition root: session store, API client, router and the E02 session lock bound to the browser lifecycle | 7.1, 7.2 | E02 T1 |
| ui/crew/src/shell/App.vue | `RouterView` keyed by the session epoch that renders a private route only with a live session; base page styles | 7.1, 7.2 | E02 T1 |
| ui/crew/src/shell/router.ts | Routes collected from `features/*/routes.ts`, a private route guard that checks the session deadline before each navigation, unknown paths to the station route | 7.1, 7.2 | E02 T1 |
| ui/crew/src/shell/session.ts | `SessionStore`: the session epoch, the memory-only personal token, one abort signal per epoch and `onClear` for stores to wipe themselves | 7.2 | E02 T1 |
| ui/crew/src/shell/api/client.ts | `CrewApi`: the generated contract client behind the session epoch; late answers from an earlier epoch are dropped (`StaleEpoch`), typed `NotSignedIn`, `ConnectionLost` and `ApiProblem`, and a keepalive sign-out with an `Idempotency-Key` | 7.3 | E02 T1 |
| ui/crew/src/design/tokens.css | The design system's `:root` tokens, copied verbatim | 7.1 | E02 T1 |
| ui/crew/src/features/profile/SignIn.vue | Station screen with an empty focusable heading; its words wait for X-E03-1 | 7.1, 7.14 | E02 T1 |
| ui/crew/src/features/profile/ProfileView.vue | Private profile route showing only the display name the hub returned | 7.14 | E02 T1 |
| ui/crew/src/features/profile/routes.ts | `/` station sign-in and `/profile` marked private | 7.1, 7.14 | E02 T1 |

Not built: `slots.ts`, `copy.ts`, `api/events.ts`, `freshness.ts`, `stage.ts`, the rest of section 7.1's shell and
design components, `StationHome.vue` and the other feature folders.

When E02 merged main at its integration pass, main had no files under ui/crew, so no shell had to be merged and these
16 files are the only crew app shell in the repository.
