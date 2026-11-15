<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { CHROME, fill } from "../../design/chrome";
import { copy } from "../../design/copy";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { placeOf } from "../today/place";
import { KIT_STATES, STATE_MK, figureMarks, loadKitMap, type KitMapView, type KitState } from "./kitMap";

const api = useCrewApi();
const stage = useStage();
const session = useSession();
const kit = ref<KitMapView | null>(null);
const failed = ref(false);
const filter = ref<KitState | "all">("all");

const rows = computed(() =>
  (kit.value?.conditions ?? []).filter((row) => filter.value === "all" || row.state === filter.value),
);
const instrumentName = computed(() => {
  const names = new Map<string, string>();
  for (const item of [...(kit.value?.kit ?? []), ...(kit.value?.candidates ?? [])]) {
    names.set(item.instrument_id, item.name);
  }
  return names;
});

function cap(text: string): string {
  return text === "" ? text : (text[0] ?? "").toUpperCase() + text.slice(1);
}

function endText(row: KitMapView["conditions"][number]): string {
  if (row.state === "needs_test_not_aboard") {
    /* the hub's own text ("This kit can't: needs an ECG"), shown from "needs" on, as the prototype does */
    const cannot = copy("kit_check_templates.cannot");
    const prefix = cannot.slice(0, cannot.indexOf("needs"));
    if (row.missing_text != null && prefix !== "" && row.missing_text.startsWith(prefix)) {
      return row.missing_text.slice(prefix.length);
    }
    const missing = row.missing_instruments.map((id) => instrumentName.value.get(id) ?? id);
    return fill(CHROME.needs, { instruments: missing.join(" and ") });
  }
  return row.state === "measured_here" ? CHROME.aboard : CHROME.noInstrument;
}

function smallText(row: KitMapView["conditions"][number]): string {
  const test = row.tests[0]?.name ?? "";
  return [test, row.body_regions.join(", ")].filter((part) => part !== "").join(" · ");
}

onMounted(async () => {
  const current = session.state.value.session;
  stage.subline = current?.person?.display_name ?? current?.actor.display_name ?? "";
  stage.headline = CHROME.kitHeadline;
  stage.focus = null;
  try {
    kit.value = await loadKitMap(api);
    stage.marks = figureMarks(kit.value);
    const place = await placeOf(api, kit.value.environment_id).catch(() => null);
    if (place !== null && stage.subline !== "") {
      stage.subline = `${stage.subline}, ${place}`;
    }
  } catch {
    failed.value = true;
  }
});

onBeforeUnmount(() => {
  stage.marks = [];
});
</script>

<template>
  <section
    class="cards flow f-kit"
    data-view="kit"
    aria-label="This kit"
  >
    <article class="card paper k-main">
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.thisKit }}
        </h2>
        <span
          v-if="kit?.example"
          class="tag"
        >{{ CHROME.exampleRows }}</span>
        <RouterLink
          class="go"
          :to="{ name: 'today' }"
          :aria-label="CHROME.backToToday"
        >
          <svg
            class="icon"
            viewBox="0 0 24 24"
          ><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>
        </RouterLink>
      </div>
      <div
        v-if="kit"
        class="flow-body"
      >
        <div
          class="kit-counts"
          data-testid="kit-counts"
        >
          <div
            v-for="state in KIT_STATES"
            :key="state"
          >
            <b class="big">{{ kit.counts[state] }}</b>
            <span><i
              class="mk"
              :class="STATE_MK[state]"
            />{{ copy(`kit_map_state_words.${state}`) }}</span>
          </div>
        </div>
        <p
          class="kit-note headline-note"
          data-testid="kit-headline-count"
        >
          {{ kit.counts.needs_test_not_aboard }} {{ fill(CHROME.ofTotal, { total: kit.counts.total }) }}
          {{ CHROME.kitNote }}
        </p>
        <div class="pills">
          <button
            class="pill"
            type="button"
            :aria-pressed="filter === 'all'"
            @click="filter = 'all'"
          >
            {{ fill(CHROME.all, { total: kit.counts.total }) }}
          </button>
          <button
            v-for="state in KIT_STATES"
            :key="state"
            class="pill"
            type="button"
            :aria-pressed="filter === state"
            @click="filter = state"
          >
            {{ copy(`kit_map_state_words.${state}`) }}
          </button>
        </div>
        <div class="list">
          <div
            v-for="row in rows"
            :key="row.condition_id"
            class="li"
          >
            <span
              class="mk"
              :class="STATE_MK[row.state]"
            />
            <span class="what">{{ row.name }}<small>{{ smallText(row) }}</small></span>
            <span class="end">{{ endText(row) }}</span>
            <a
              v-if="row.cliff_url"
              class="cliff"
              :href="row.cliff_url"
              target="_blank"
              rel="noopener"
            >{{ CHROME.form }}</a>
          </div>
        </div>
      </div>
      <p
        v-else-if="failed"
        class="kit-note"
      >
        {{ CHROME.kitMapUnavailable }}
      </p>
    </article>
    <article
      v-if="kit"
      class="card g g-sun k-aboard"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.kitAboard }}
        </h2>
      </div>
      <div class="flow-body">
        <div
          id="km-aboard"
          class="list"
        >
          <div
            v-for="item in kit.kit"
            :key="item.instrument_id"
            class="li"
          >
            <span class="what">{{ cap(item.name) }}</span>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.f-kit .k-main {
  grid-row: 1 / span 3;
}
.f-kit .k-aboard {
  grid-row: 1 / span 2;
}
.flow-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}
.flow-body .list {
  overflow: auto;
  min-height: 0;
}
.headline-note {
  margin-top: 10px;
}
</style>
