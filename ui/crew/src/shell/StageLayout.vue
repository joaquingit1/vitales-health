<script setup lang="ts">
import type { components } from "@crew-health/contracts";
import { computed, inject, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import BodyFigure from "../design/BodyFigure.vue";
import { CHROME } from "../design/chrome";
import { copy } from "../design/copy";
import { CREW_API } from "./api/client";
import { placeOf } from "../features/today/place";
import type { GroundLink } from "../features/today/groundLink";
import { useSession } from "./session";
import { useStage } from "./stage";

const stage = useStage();
const session = useSession();
const stageEl = ref<HTMLElement | null>(null);
const who = computed(() => {
  const current = session.state.value.session;
  return current?.person?.display_name ?? current?.actor.display_name ?? "";
});
const initials = computed(() =>
  who.value
    .split(" ")
    .filter((word) => word !== "")
    .map((word) => word[0] ?? "")
    .join("")
    .slice(0, 2),
);
const kitMode = computed(() => stage.marks.length > 0);

type Capability = components["schemas"]["capability.schema"];
/* optional here: the stage also frames the sign-in station, which a shell may mount before any hub client */
const api = inject(CREW_API, null);
/* The stage's three readings (UX-DESIGN 1.2): the first three measurements this place can take, from the hub.
   The hub serves no recent value to the stage, so each reads as the designed state "No reading". */
const readings = ref<{ code: string; label: string }[]>([]);
/* The who-line ends with the place, as the reference writes it ("Mara Okafor, flight day 19 of 30, space station"):
   the active environment from the hub's ground link, its place word from the environment profiles. */
const place = ref<string | null>(null);
/* A configured label at the title position reads in sentence case ("Camera station"), as the reference titles do;
   the words themselves are the configuration's. */
const headline = computed(() =>
  stage.headline === "" ? "" : (stage.headline[0] ?? "").toUpperCase() + stage.headline.slice(1),
);
const whoLine = computed(() => [stage.subline, place.value ?? ""].filter((part) => part !== "").join(", "));
onMounted(() => {
  if (api === null || session.state.value.session === null) {
    return;
  }
  api
    .get<GroundLink>("/v1/ground-link")
    .then((link) => placeOf(api, link.environment_id))
    .then((value) => {
      place.value = value;
    })
    .catch(() => {
      place.value = null;
    });
  api
    .get<{ items: Capability[] }>("/v1/capabilities")
    .then((value) => {
      readings.value = value.items
        .filter((item) => item.status === "available" && item.body_region !== null)
        .slice(0, 3)
        .map((item) => ({ code: item.code, label: item.label }));
    })
    .catch(() => {
      readings.value = [];
    });
});
</script>

<template>
  <div
    class="app"
    data-testid="stage-layout"
  >
    <section
      ref="stageEl"
      class="stage"
      :class="{ 'kit-mode': kitMode }"
      aria-label="Body view"
    >
      <BodyFigure
        :marks="stage.marks"
        :focus="stage.focus"
        :stage-el="stageEl"
      />
      <header class="stage-head">
        <RouterLink
          class="brand-mark"
          :to="{ name: 'today' }"
          :aria-label="copy('display_group_labels.today')"
        >
          <svg
            viewBox="0 0 18 18"
            width="18"
            height="18"
            fill="none"
          ><path
            d="M13.6 3.4a7 7 0 1 1-9.2 0"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          /><circle
            cx="9"
            cy="2.6"
            r="1.5"
            fill="currentColor"
          /></svg>
        </RouterLink>
        <span class="brand">{{ CHROME.brand }}</span>
        <RouterLink
          class="urgent-btn"
          :to="{ name: 'help' }"
        >
          {{ CHROME.someoneNeedsHelp }}
        </RouterLink>
        <RouterLink
          v-if="who !== ''"
          class="who"
          :to="{ name: 'profile' }"
          :aria-label="who"
        >
          {{ initials }}
        </RouterLink>
      </header>
      <div class="title rest">
        <div
          class="who-line"
          data-testid="stage-subline"
        >
          {{ whoLine }}
        </div>
        <h1 data-testid="stage-headline">
          {{ headline }}
        </h1>
      </div>
      <div
        v-if="readings.length > 0"
        class="vitals"
        data-testid="stage-readings"
      >
        <div
          v-for="reading in readings"
          :key="reading.code"
          class="vital"
        >
          <span class="v-num word">{{ copy("designed_states.no_reading") }}</span>
          <span class="v-label">{{ reading.label }}</span>
        </div>
      </div>
    </section>
    <div class="side">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.brand-mark,
.who {
  text-decoration: none;
  color: inherit;
}
.who {
  display: grid;
  place-items: center;
}
.v-num.word {
  font-size: 20px;
  color: var(--ink-3);
}
.urgent-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}
</style>
