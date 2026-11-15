<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { CHROME } from "../../../design/chrome";
import { copy } from "../../../design/copy";
import SourceWord from "../../../design/SourceWord.vue";
import { ApiProblem, useCrewApi, type Health } from "../../../shell/api/client";
import { linkLine, linkOrigin, type GroundLink } from "../groundLink";
import { placeOf } from "../place";
import HabitatTiles from "./HabitatTiles.vue";

/* The Habitat card (reference 01-today.png): the place and the ground link line, then the channel tiles. The link
   is read again every few seconds so the line turns with the operator's demo switch while Today is on screen. */
const api = useCrewApi();
const link = ref<GroundLink | null>(null);
const linkFailed = ref(false);
const hubNow = ref<number>(Number.NaN);
const place = ref<string | null>(null);
const line = computed(() => (link.value === null ? "" : linkLine(link.value, hubNow.value)));
const LINK_POLL_MS = 2000;
let poll = 0;

/* a place name opens the habitat line, so it takes a capital as the reference writes it ("Space station") */
function sentence(text: string): string {
  return text === "" ? text : (text[0] ?? "").toUpperCase() + text.slice(1);
}

async function read(): Promise<void> {
  const [value, health] = await Promise.all([
    api.get<GroundLink>("/v1/ground-link"),
    api.get<Health>("/v1/health").catch(() => null),
  ]);
  link.value = value;
  hubNow.value = Date.parse(health?.server_time ?? "");
}

onMounted(async () => {
  poll = window.setInterval(() => void read().catch(() => undefined), LINK_POLL_MS);
  try {
    await read();
  } catch (error) {
    linkFailed.value = error instanceof ApiProblem || error instanceof Error;
  }
  if (link.value !== null) {
    place.value = await placeOf(api, link.value.environment_id).catch(() => null);
  }
});
onBeforeUnmount(() => window.clearInterval(poll));
</script>

<template>
  <RouterLink
    class="card g g-dusk habitat"
    :to="{ name: 'today' }"
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
    <p
      v-if="link?.delay_text"
      class="sub delay"
    >
      {{ link.delay_text }}
    </p>
    <HabitatTiles />
  </RouterLink>
</template>

<style scoped>
.env-line .src {
  margin-left: 6px;
}
</style>
