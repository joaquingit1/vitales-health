<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { CHROME, fill } from "../../design/chrome";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import { ApiProblem, useCrewApi, type Health } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { todayCards } from "../../shell/todaySlots";
import { KIT_STATES, STATE_MK, loadKitMap, type KitMapView } from "../kit/kitMap";
import { linkLine, linkOrigin, type GroundLink } from "./groundLink";
import { placeOf } from "./place";
import { ASSISTANT_TITLE } from "../help/labels";
import { assistantWords } from "../help/helpModel";

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
/* a place name opens the habitat line, so it takes a capital as the reference writes it ("Space station") */
function sentence(text: string): string {
  return text === "" ? text : (text[0] ?? "").toUpperCase() + text.slice(1);
}
const assistantState = ref<string | null>(null);
const assistant = computed(() => (assistantState.value === null ? null : assistantWords(assistantState.value)));
const LINK_POLL_MS = 2000;
let poll = 0;

const name = computed(() => {
  const current = session.state.value.session;
  return current?.person?.display_name ?? current?.actor.display_name ?? "";
});
const line = computed(() => (link.value === null ? "" : linkLine(link.value, hubNow.value)));

/* Slot positions in the reference grid: the prototype's How you are and Daily check places. The Assistant place
   holds the assistant card. A reading about you takes the How you are card's sun gradient (UX-DESIGN 1.5). */
const SLOT_AREAS = ["11 / span 14 | 1", "9 / span 7 | 3"];
const SLOT_KIND = ["g g-sun", "paper"];
function slotStyle(index: number): Record<string, string> {
  const [column = "auto", row = "auto"] = (SLOT_AREAS[index] ?? "auto | auto").split(" | ");
  return { gridColumn: column, gridRow: row };
}

function setStage(): void {
  stage.marks = [];
  stage.focus = null;
  /* the reference's who-line: the person, then the place; the headline names the view */
  stage.subline = place.value === null || name.value === "" ? name.value : `${name.value}, ${place.value}`;
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
    (value) => {
      kit.value = value;
    },
    () => {
      kitFailed.value = true;
    },
  );
  await Promise.all([health, linkRead, kitRead]);
  const environment = link.value?.environment_id ?? kit.value?.environment_id ?? null;
  if (environment !== null) {
    place.value = await placeOf(api, environment).catch(() => null);
  }
  setStage();
});

onBeforeUnmount(() => {
  window.clearInterval(poll);
});
</script>

<template>
  <section
    class="cards"
    data-view="today"
    aria-label="Today"
  >
    <article
      class="card paper todo"
      data-testid="todo-card"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.todo }}
        </h2>
      </div>
      <p
        class="sub"
        data-testid="todo-state"
      >
        {{ copy("designed_states.not_available_here") }}
      </p>
    </article>

    <article
      class="card g g-dusk habitat"
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
    </article>

    <RouterLink
      class="card paper kit-card"
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
        <div class="kit-legend">
          <span
            v-for="state in KIT_STATES"
            :key="state"
          ><i
            class="mk"
            :class="STATE_MK[state]"
          />{{ kit.counts[state] }} {{ copy(`kit_map_state_words.${state}`).toLowerCase() }}</span>
        </div>
        <p class="kit-note">
          {{ copy(kit.plans_to_write.length > 0 ? "plan_lines.plans_to_write" : "plan_lines.plans_aboard", { count: kit.plans_aboard, total: kit.counts.total, missing: kit.plans_to_write.length }) }}
        </p>
      </template>
      <p
        v-else-if="kitFailed"
        class="kit-note"
      >
        {{ copy("designed_states.not_available_here") }}
      </p>
    </RouterLink>

    <article
      v-for="(slot, index) in slots"
      :key="slot.id"
      class="card slot-card"
      :class="SLOT_KIND[index] ?? 'paper'"
      :style="slotStyle(index)"
      :data-slot="slot.id"
    >
      <component :is="slot.component" />
    </article>

    <article
      v-if="assistant"
      class="card g g-deep assistant"
      data-testid="assistant-card"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ ASSISTANT_TITLE }}
        </h2>
      </div>
      <p class="say">
        {{ assistant.line }}
      </p>
    </article>
  </section>
</template>

<style scoped>
.kit-card {
  color: inherit;
  text-decoration: none;
}
.env-line .src {
  margin-left: 6px;
}
/* a feature card with nothing to show today leaves its place empty, never an empty frame */
.slot-card:empty {
  display: none;
}
/* with the Daily check place empty, the assistant closes the row up to This kit */
.cards:not(:has(.slot-card[data-slot="teach"]:not(:empty))) .assistant {
  grid-column: 9 / span 16;
}
</style>
