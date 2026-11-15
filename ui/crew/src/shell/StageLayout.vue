<script setup lang="ts">
import type { components } from "@crew-health/contracts";
import { computed, inject, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import BodyFigure from "../design/BodyFigure.vue";
import { copy } from "../design/copy";
import { SESSION_LOCK } from "../features/identity/sessionLock";
import { KIT_STATES, STATE_MK, figureMarks, loadKitMap, type KitMapView } from "../features/kit/kitMap";
import { CREW_API } from "./api/client";
import ContextBar from "./context/ContextBar.vue";
import { useSession } from "./session";
import { useStage } from "./stage";

/**
 * Every signed-in screen: the context bar across the top, a narrow body panel on the left and the screen's own
 * content on the right. The body panel is a tool, not decoration: its marks are the kit map's per-region marks
 * (what this kit measures, what rests on self-report, what needs a test not aboard), each mark and legend row
 * opens the kit view filtered to it, and the latest readings sit under it.
 */
type Capability = components["schemas"]["capability.schema"];

const stage = useStage();
const session = useSession();
const lock = inject(SESSION_LOCK, null);
const api = inject(CREW_API, null);
const stageEl = ref<HTMLElement | null>(null);
const who = computed(() => {
  const current = session.state.value.session;
  return current?.person?.display_name ?? current?.actor.display_name ?? "";
});
const kit = ref<KitMapView | null>(null);
const readings = ref<{ code: string; label: string; region: string | null }[]>([]);
/* a screen that sets its own marks (the kit view, a measurement) keeps them; otherwise the kit map's marks */
const marks = computed(() => (stage.marks.length > 0 ? stage.marks : kit.value === null ? [] : figureMarks(kit.value)));
const headline = computed(() =>
  stage.headline === "" ? "" : (stage.headline[0] ?? "").toUpperCase() + stage.headline.slice(1),
);
const read = <T,>(path: string): Promise<T> => (api === null ? Promise.reject(new Error("no hub client")) : api.get<T>(path));

onMounted(() => {
  if (api === null || session.state.value.session === null) {
    return;
  }
  loadKitMap(api).then(
    (value) => (kit.value = value),
    () => (kit.value = null),
  );
  api
    .get<{ items: Capability[] }>("/v1/capabilities")
    .then((value) => {
      readings.value = value.items
        .filter((item) => item.status === "available" && item.body_region !== null)
        .slice(0, 4)
        .map((item) => ({ code: item.code, label: item.label, region: item.body_region }));
    })
    .catch(() => {
      readings.value = [];
    });
});

function signOut(): void {
  void lock?.signOut();
}
</script>

<template>
  <div
    class="app shell"
    data-testid="stage-layout"
  >
    <ContextBar
      class="shell-bar"
      :read="read"
      :who="who"
      home-to="/today"
      help-to="/help"
      @sign-out="signOut"
    />
    <aside
      ref="stageEl"
      class="stage body-panel kit-mode"
      aria-label="Body view"
    >
      <div class="title rest">
        <div
          class="who-line"
          data-testid="stage-subline"
        >
          {{ stage.subline }}
        </div>
        <h1 data-testid="stage-headline">
          {{ headline }}
        </h1>
      </div>
      <div class="figure-box">
        <BodyFigure
          :marks="marks"
          :focus="stage.focus"
          :stage-el="stageEl"
        />
      </div>
      <nav
        v-if="kit"
        class="panel-legend"
        :aria-label="copy('context_words.body_title')"
        data-testid="body-legend"
      >
        <p class="panel-title">
          {{ copy("context_words.body_title") }}
        </p>
        <RouterLink
          v-for="state in KIT_STATES"
          :key="state"
          class="legend-row"
          :to="{ name: 'kit', query: { state } }"
        >
          <i
            class="mk"
            :class="STATE_MK[state]"
          />
          <span>{{ copy(`kit_map_state_words.${state}`) }}</span>
          <b>{{ kit.counts[state] }}</b>
        </RouterLink>
      </nav>
      <div
        v-if="readings.length > 0"
        class="panel-readings"
        data-testid="stage-readings"
      >
        <p class="panel-title">
          {{ copy("context_words.latest_readings") }}
        </p>
        <RouterLink
          v-for="reading in readings"
          :key="reading.code"
          class="legend-row"
          :to="reading.region === null ? { name: 'kit' } : { name: 'kit', query: { region: reading.region } }"
        >
          <span>{{ reading.label }}</span>
          <b class="word">{{ copy("designed_states.no_reading") }}</b>
        </RouterLink>
      </div>
    </aside>
    <div class="side">
      <slot />
    </div>
  </div>
</template>

<style>
.app.shell { grid-template-columns: 300px minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); height: 100vh; min-height: 760px; }
.app.shell > .shell-bar { grid-column: 1 / -1; }
.app.shell > .side { grid-row: 2; }
.app.shell .side > .cards { padding: 14px 16px 16px 4px; }
.body-panel { grid-row: 2; display: flex; flex-direction: column; padding: 14px 16px 16px; --fig-top: 8px; --fig-bottom: 8px; --mask-a: 0px; --mask-b: 0px; overflow: hidden; }
.body-panel .title { position: static; text-align: left; }
.body-panel .title h1 { margin-top: 2px; font-size: 26px; line-height: 32px; }
.body-panel .figure-box { position: relative; overflow: hidden; flex: 1 1 auto; min-height: 220px; }
.body-panel .figure-box #body { -webkit-mask-image: none; mask-image: none; }
.body-panel .spot.kit-spot { pointer-events: none; }
.panel-legend, .panel-readings { margin-top: 10px; display: grid; gap: 2px; }
.panel-title { font-size: 12px; line-height: 16px; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.legend-row { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 10px; color: inherit; text-decoration: none; font-size: 13px; line-height: 18px; }
.legend-row:hover { background: rgba(28, 30, 28, 0.05); }
.legend-row b { margin-left: auto; font-weight: 500; font-variant-numeric: tabular-nums; }
.legend-row b.word { font-weight: 400; color: var(--ink-3); }
</style>
