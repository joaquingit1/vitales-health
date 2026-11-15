<script setup lang="ts">
import type { components } from "@crew-health/contracts";
import { computed, onBeforeUnmount, onMounted, ref, watch, type Component } from "vue";
import { RouterLink } from "vue-router";
import { CHROME, fill } from "../../design/chrome";
import { copy } from "../../design/copy";
import { refusalText } from "../../design/refusal";
import SourceWord from "../../design/SourceWord.vue";
import { ApiProblem, useCrewApi, type Health } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { todayCards } from "../../shell/todaySlots";
import { KIT_STATES, STATE_MK, instrumentNames, loadKitMap, type KitMapView } from "../kit/kitMap";
import { linkLine, linkOrigin, type GroundLink } from "./groundLink";
import { placeOf } from "./place";
import { ASSISTANT_TITLE } from "../help/labels";
import assistantUi from "../../../../../config/assistant-ui.json";
import AssistantConversation from "./AssistantConversation.vue";

/* The cards the Today cards work fills (features/today/cards/HabitatCard.vue, HowYouAreCard.vue, PulseCard.vue):
   each takes its place in the grid as soon as its file exists; until then the place shows this view's own card. */
const cardFiles = import.meta.glob<Component>("./cards/*.vue", { eager: true, import: "default" });
function card(name: string): Component | null {
  return cardFiles[`./cards/${name}.vue`] ?? null;
}
const habitatCard = card("HabitatCard");
const howYouAreCard = card("HowYouAreCard");
const pulseCard = card("PulseCard");

const api = useCrewApi();
const stage = useStage();
const session = useSession();
const slots = todayCards();

const kit = ref<KitMapView | null>(null);
const kitFailed = ref(false);
const link = ref<GroundLink | null>(null);
const linkFailed = ref(false);
const hubNow = ref<number>(Number.NaN);
const place = ref<string | null>(null);
watch(
  () => link.value?.environment_id ?? kit.value?.environment_id ?? null,
  async (environment, _previous, onCleanup) => {
    let current = true;
    onCleanup(() => { current = false; });
    place.value = null;
    if (environment !== null) {
      const name = await placeOf(api, environment).catch(() => null);
      if (current) place.value = name;
    }
  },
);
/* a place name opens the habitat line, so it takes a capital as the reference writes it ("Space station") */
function sentence(text: string): string {
  return text === "" ? text : (text[0] ?? "").toUpperCase() + text.slice(1);
}
/* The assistant's true state, from health (which reads the assistant module's own runtime record, like
   GET /v1/assistant/status and the station card). Running: the card names where it runs and takes a message. */
const assistantState = ref<string | null>(null);
const assistantLine = computed((): string | null => {
  switch (assistantState.value) {
    case null:
      return null;
    case "running":
      return copy("context_words.assistant_running");
    case "loading":
      return assistantUi.states.loading;
    case "stopped":
      return copy("designed_states.assistant_stopped");
    default:
      return copy("error_messages.MODEL_UNAVAILABLE");
  }
});
const instruments = ref<ReadonlyMap<string, string>>(new Map());
/* The To do line: what the routine has due today (getRoutineToday), its designed refusal, or nothing while it loads. */
type RoutineSchedule = components["schemas"]["routine-schedule.schema"];
const todo = ref("");
function todoLine(schedule: RoutineSchedule): string {
  const due = schedule.counts.due;
  return due > 0 ? `${String(due)} ${copy("occurrence_end_text.due_today")}` : copy("designed_states.nothing_due");
}
const LINK_POLL_MS = 2000;
let poll = 0;

const name = computed(() => {
  const current = session.state.value.session;
  return current?.person?.display_name ?? current?.actor.display_name ?? "";
});
const line = computed(() => (link.value === null ? "" : linkLine(link.value, hubNow.value)));

const pulseSlot = slots.find((slot) => slot.id === "pulse") ?? null;
const otherSlots = slots.filter((slot) => slot.id !== "pulse");

function setStage(): void {
  stage.marks = [];
  stage.focus = null;
  /* the person; the stage adds the place to the who-line. The headline names the view. */
  stage.subline = name.value;
  stage.headline = copy("display_group_labels.today");
}

onMounted(async () => {
  setStage();
  /* The link changes on the hub (the operator's demo switch, the schedule): read it again every few seconds so
     the Habitat line turns while Today is on screen. Only the line changes. */
  poll = window.setInterval(() => {
    void Promise.all([
      api.get<GroundLink>("/v1/ground-link").then((value) => {
        link.value = value;
      }),
      api.get<Health>("/v1/health").then((value) => {
        hubNow.value = Date.parse(value.server_time ?? "");
        assistantState.value = value.assistant_state ?? null;
      }),
    ]).catch(() => undefined);
  }, LINK_POLL_MS);
  const health = api.get<Health>("/v1/health").then(
    (value) => {
      hubNow.value = Date.parse(value.server_time ?? "");
      assistantState.value = value.assistant_state ?? null;
    },
    () => undefined,
  );
  const linkRead = api.get<GroundLink>("/v1/ground-link").then(
    (value) => {
      link.value = value;
    },
    (error: unknown) => {
      linkFailed.value = error instanceof ApiProblem || error instanceof Error;
    },
  );
  const kitRead = loadKitMap(api).then(
    async (value) => {
      kit.value = value;
      instruments.value = await instrumentNames(api, value).catch(() => new Map<string, string>());
    },
    () => {
      kitFailed.value = true;
    },
  );
  const routineRead = api.get<RoutineSchedule>("/v1/routine/today").then(
    (value) => {
      todo.value = todoLine(value);
    },
    (error: unknown) => {
      todo.value = refusalText(error instanceof ApiProblem ? error.code : null);
    },
  );
  await Promise.all([health, linkRead, kitRead, routineRead]);
  setStage();
});

onBeforeUnmount(() => {
  window.clearInterval(poll);
});
</script>

<template>
  <section
    class="cards today-grid"
    data-view="today"
    aria-label="Today"
  >
    <!-- C: the pulse check -->
    <article
      v-if="pulseCard || pulseSlot"
      class="card g g-sun area-pulse slot-card"
      :data-slot="pulseSlot?.id ?? 'pulse'"
    >
      <component :is="pulseCard ?? pulseSlot?.component" />
    </article>

    <!-- B: where you are and whether Earth can hear you -->
    <article
      class="card g g-dusk habitat area-habitat"
      data-testid="habitat-card"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.habitat }}
        </h2>
      </div>
      <p
        v-if="link"
        class="env-line"
        data-testid="habitat-line"
      >
        <template v-if="place">
          {{ sentence(place) }}.
        </template>
        {{ line }}
        <SourceWord :origin="linkOrigin(link)" />
      </p>
      <p
        v-else-if="linkFailed"
        class="env-line"
      >
        {{ copy("designed_states.unknown") }}
      </p>
      <component
        :is="habitatCard"
        v-if="habitatCard"
      />
    </article>

    <!-- A: what this kit can and can't test -->
    <RouterLink
      class="card paper kit-card area-kit"
      :to="{ name: 'kit' }"
      data-testid="kit-card"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.thisKit }}
        </h2>
        <span
          v-if="kit?.example"
          class="tag"
        >{{ CHROME.exampleRows }}</span>
        <span class="go"><svg
          class="icon"
          viewBox="0 0 24 24"
        ><path d="M7 7h10v10" /><path d="M7 17 17 7" /></svg></span>
      </div>
      <template v-if="kit">
        <div
          class="big"
          data-testid="kit-count"
        >
          {{ kit.counts.needs_test_not_aboard }}<small>{{ fill(CHROME.ofTotal, { total: kit.counts.total }) }}</small>
        </div>
        <p class="kit-note">
          {{ CHROME.kitNote }}
        </p>
        <div
          class="cover-bar"
          aria-hidden="true"
        >
          <i
            v-for="state in KIT_STATES"
            :key="state"
            :class="STATE_MK[state]"
            :style="{ flexGrow: kit.counts[state] }"
          />
        </div>
        <div class="kit-legend">
          <div
            v-for="state in KIT_STATES"
            :key="state"
          >
            <b>{{ kit.counts[state] }}</b>
            <span><i
              class="mk"
              :class="STATE_MK[state]"
            />{{ copy(`kit_map_state_words.${state}`) }}</span>
          </div>
        </div>
      </template>
      <p
        v-else-if="kitFailed"
        class="kit-note"
      >
        {{ copy("designed_states.not_available_here") }}
      </p>
    </RouterLink>

    <article
      v-if="howYouAreCard"
      class="card paper area-howyou"
    >
      <component :is="howYouAreCard" />
    </article>

    <!-- attention items: one line from the routine's counts (E25 brings the full card) -->
    <div
      class="todo-row area-todo"
      data-testid="todo-card"
    >
      <span class="todo-title">{{ CHROME.todo }}</span>
      <span
        class="todo-state"
        data-testid="todo-state"
      >{{ todo }}</span>
      <article
        v-for="slot in otherSlots"
        :key="slot.id"
        class="card paper inner-slot slot-card"
        :data-slot="slot.id"
      >
        <component :is="slot.component" />
      </article>
    </div>

    <!-- D: someone needs help, then the plan aboard, then teach a crewmate -->
    <RouterLink
      class="card help-card area-help"
      :to="{ name: 'help' }"
      data-testid="today-help"
    >
      <h2 class="help-word">
        {{ CHROME.someoneNeedsHelp }}
      </h2>
      <p
        v-if="kit"
        class="sub"
      >
        {{ copy("context_words.plans_line", { count: kit.plans_aboard, total: kit.counts.total }) }}
      </p>
    </RouterLink>

    <!-- E: the assistant's true state -->
    <article
      v-if="assistantLine !== null"
      class="card g g-deep assistant area-assistant"
      :data-state="assistantState"
      data-testid="assistant-card"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ ASSISTANT_TITLE }}
        </h2>
      </div>
      <p class="say">
        {{ assistantLine }}
      </p>
      <AssistantConversation
        :running="assistantState === 'running'"
        :instruments="instruments"
      />
    </article>
  </section>
</template>

<style scoped>
.today-grid {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  /* the to do line keeps its own height; below it two rows of the same height, the lower one split between
     Someone needs help and the assistant, so both halves of the screen end on the same lines */
  grid-template-rows: max-content minmax(0, 1fr) minmax(0, 0.5fr) minmax(0, 0.5fr);
  overflow-y: auto;
  grid-template-areas:
    "todo todo todo todo todo todo"
    "pulse pulse pulse habitat habitat habitat"
    "kit kit howyou howyou help help"
    "kit kit howyou howyou assistant assistant";
}
.today-grid:not(:has(.area-howyou)) {
  grid-template-areas:
    "todo todo todo todo todo todo"
    "pulse pulse pulse habitat habitat habitat"
    "kit kit kit help help help"
    "kit kit kit assistant assistant assistant";
}
/* A conversation on the Assistant card takes the lower left; Someone needs help keeps its place. */
.today-grid:has(.ask.active) {
  grid-template-areas:
    "todo todo todo todo todo todo"
    "pulse pulse pulse habitat habitat habitat"
    "assistant assistant assistant help help help"
    "assistant assistant assistant kit kit howyou";
}
.today-grid:not(:has(.area-howyou)):has(.ask.active) {
  grid-template-areas:
    "todo todo todo todo todo todo"
    "pulse pulse pulse habitat habitat habitat"
    "assistant assistant assistant help help help"
    "assistant assistant assistant kit kit kit";
}
.area-pulse { grid-area: pulse; }
.area-habitat { grid-area: habitat; }
.area-kit { grid-area: kit; }
.area-howyou { grid-area: howyou; }
.area-todo { grid-area: todo; }
.area-help { grid-area: help; }
.area-assistant { grid-area: assistant; }
.kit-card,
.habitat,
.help-card {
  color: inherit;
  text-decoration: none;
}
.habitat { color: #fff; }
.env-line { font-size: 20px; line-height: 26px; margin-top: 8px; }
.env-line .src {
  margin-left: 6px;
}
.help-card { background: rgba(180, 105, 14, 0.12); box-shadow: inset 0 0 0 1px rgba(180, 105, 14, 0.25); justify-content: center; gap: 8px; }
.help-card:hover { background: rgba(180, 105, 14, 0.2); }
.help-word { font-size: 30px; line-height: 36px; font-weight: 500; color: #8a4f08; }
.help-card .sub { color: #8a4f08; }
.todo-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 10px 18px; border-radius: 16px; background: var(--paper); box-shadow: inset 0 0 0 1px var(--line); }
.todo-title { font-size: 15px; font-weight: 500; }
.todo-state { font-size: 14px; color: var(--ink-3); }
.inner-slot { flex: 1 1 100%; padding: 14px 16px; }
/* the three counts under the coverage bar, one column each, as on the station screen */
.kit-card .kit-legend { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 0; padding-top: 0; font-size: 14px; line-height: 18px; color: var(--ink-2); }
.kit-card .kit-legend b { display: block; margin-bottom: 4px; font-size: 32px; line-height: 36px; font-weight: 300; color: var(--ink); font-variant-numeric: tabular-nums; }
.kit-card .cover-bar { margin: 18px 0 14px; }
.inner-slot:empty { display: none; }
.slot-card:empty {
  display: none;
}
@media (max-width: 720px) {
  .cards.today-grid { display: flex; flex-direction: column; }
  .today-grid > .card { height: auto; min-height: 180px; }
  .today-grid > .habitat { min-height: 260px; }
  .area-todo { order: -1; }
}
</style>
