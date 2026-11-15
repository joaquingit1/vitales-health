# Crew app: demo extension points

The shell (ui/crew/src/shell, ui/crew/src/design) belongs to ui-shell. Feature builders add files only inside
their own feature folder; nobody else edits shell or design files.

## Routes

`src/shell/router.ts` collects every `src/features/*/routes.ts` that exports `routes: RouteRecordRaw[]`.
These placeholders already exist and render only their route name:

| Route        | Name    | Folder (owner)                      |
| ------------ | ------- | ----------------------------------- |
| `/pulse`     | `pulse` | `features/pulse` (ui-pulse-teach)   |
| `/teach/:id` | `teach` | `features/teach` (ui-pulse-teach)   |
| `/plan/:id`  | `plan`  | `features/plan` (ui-plan-help)      |
| `/help`      | `help`  | `features/help` (ui-plan-help)      |

To ship the real screen, replace `features/<name>/<Name>Placeholder.vue` (e.g. `PulsePlaceholder.vue`) (or point `routes.ts` at your own
component). Keep `meta: { private: true }`: the router sends a signed-out visitor to the sign-in station.

## Today card slot

Create `src/features/<name>/today.ts`:

```ts
import type { TodayCard } from "../../shell/todaySlots";
import PulseCard from "./PulseCard.vue";

export const todayCards: TodayCard[] = [{ id: "pulse", order: 10, component: PulseCard }];
```

Today renders every registered card, sorted by `order` (pulse 10, teach 20, plan 30, help 40), inside a
`.card` frame. The card component owns its content and its data.

## Hub access

```ts
import { useCrewApi } from "../../shell/api/client";
const api = useCrewApi();
const map = await api.get<components["schemas"]["KitMap"]>("/v1/kit/map");
await api.post<Result>("/v1/some/command", body); // adds Idempotency-Key
```

Calls are session fenced: a result from an ended session epoch throws `StaleEpoch`, a hub error throws
`ApiProblem(status, code)`. Show the designed empty or refused state on errors; never invent values.

## Design system

`src/design/tokens.css` holds the prototype tokens and fonts; `src/design/reader.css` holds the shared
classes (`.card`, `.src` source word, `.eyebrow`, `.mono`). Every value carries its source word
(Live, Sim, Replay): use `<SourceWord :origin="..." />` from `src/design/SourceWord.vue`.
All visible copy comes from `config/ui-copy.json` through `src/design/copy.ts` (`copy("key.path")`).

## The stage (body figure)

Every private route renders beside the body stage (`src/shell/StageLayout.vue`) unless its route sets
`meta: { stage: false }`. Your screen is the right-hand side: render a `<section class="cards flow f-...">`
grid of `.card`s, as the prototype's views do. Drive the left side with `useStage()` from
`src/shell/stage.ts`:

```ts
const stage = useStage();
stage.headline = "...";          // the big line above the figure
stage.subline = "...";           // the small line above it
stage.focus = "heart";           // light a region and move in (null: whole body)
stage.marks = [{ region: "heart", kind: "open" }]; // dot | hollow | open; clear on unmount
```

The figure stays mounted across stage routes, so it builds from points only once per session.

## Screens owned by ui-shell

`/` sign in (crew person id + credential, E02 session), `/today` (To do, Habitat with the ground link line,
This kit, then the Today card slots) and `/kit` (shot A: counts, rows, figure marks from `/v1/kit-map`).
