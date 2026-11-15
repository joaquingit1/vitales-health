<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { CHROME, fill } from "../../design/chrome";
import { copy } from "../../design/copy";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import { useStage } from "../../shell/stage";
import { KIT_STATES, STATE_MK, figureMarks, instrumentNames, loadKitMap, type KitMapView, type KitState } from "./kitMap";

const api = useCrewApi();
const stage = useStage();
const session = useSession();
const kit = ref<KitMapView | null>(null);
const failed = ref(false);
const route = useRoute();
/* the body panel's legend and readings open this view filtered: ?state=<kit state>, ?region=<body region> */
function askedState(): KitState | "all" {
  const asked = route.query["state"];
  return typeof asked === "string" && (KIT_STATES as readonly string[]).includes(asked) ? (asked as KitState) : "all";
}
function askedRegion(): string | null {
  const asked = route.query["region"];
  return typeof asked === "string" ? asked : null;
}
const filter = ref<KitState | "all">(askedState());
const region = ref<string | null>(askedRegion());
watch(
  () => route.query,
  () => {
    filter.value = askedState();
    region.value = askedRegion();
    stage.focus = region.value;
  },
);

const rows = computed(() =>
  (kit.value?.conditions ?? []).filter(
    (row) =>
      (filter.value === "all" || row.state === filter.value) &&
      (region.value === null || (row.body_regions as readonly string[]).includes(region.value)),
  ),
);
const instrumentName = ref<Map<string, string>>(new Map());

function cap(text: string): string {
  return text === "" ? text : (text[0] ?? "").toUpperCase() + text.slice(1);
}

/* ui-copy kit_check_templates.cannot ("This kit can't: needs {instruments}"), shown from its verb on, as the
   prototype's row end does ("needs an ECG"). */
function cannotTail(text: string): string {
  const cannot = copy("kit_check_templates.cannot");
  const prefix = cannot.slice(0, cannot.indexOf("{instruments}")).replace(/\S+\s*$/, "");
  return prefix !== "" && text.startsWith(prefix) ? text.slice(prefix.length) : text;
}

function endText(row: KitMapView["conditions"][number]): string {
  if (row.state === "needs_test_not_aboard") {
    if (row.missing_text != null && row.missing_text !== "") {
      return cannotTail(row.missing_text);
    }
    const missing = row.missing_instruments
      .map((id) => instrumentName.value.get(id))
      .filter((name): name is string => name !== undefined);
    return missing.length === 0
      ? copy("kit_map_state_words.needs_test_not_aboard")
      : cannotTail(copy("kit_check_templates.cannot", { instruments: missing.join(" and ") }));
  }
  return copy(`kit_map_state_words.${row.state}`);
}

/* What would close the most gaps: each instrument the hub lists as not aboard, with the conditions it would
   bring within reach, counted from the same kit map. Most first. */
const closers = computed(() =>
  (kit.value?.candidates ?? [])
    .filter((item) => !item.aboard)
    .map((item) => ({
      id: item.instrument_id,
      name: cap(item.name),
      closes: (kit.value?.conditions ?? []).filter((row) => row.missing_instruments.includes(item.instrument_id))
        .length,
    }))
    .sort((a, b) => b.closes - a.closes || a.name.localeCompare(b.name)),
);

function regionLabel(region: string): string {
  const label = copy(`body_region_labels.${region}`);
  return label.startsWith("body_region_labels.") ? region : label.toLowerCase();
}

function smallText(row: KitMapView["conditions"][number]): string {
  const test = row.tests[0]?.name ?? "";
  return [test, row.body_regions.map(regionLabel).join(", ")].filter((part) => part !== "").join(" · ");
}

onMounted(async () => {
  const current = session.state.value.session;
  stage.subline = current?.person?.display_name ?? current?.actor.display_name ?? "";
  stage.headline = CHROME.kitHeadline;
  stage.focus = region.value;
  try {
    const view = await loadKitMap(api);
    instrumentName.value = await instrumentNames(api, view);
    kit.value = view;
    stage.marks = figureMarks(kit.value);
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
          :aria-label="copy('display_group_labels.today')"
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
        {{ copy("designed_states.not_available_here") }}
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
    <article
      v-if="kit && closers.length > 0"
      class="card g g-deep k-close"
      data-testid="kit-close"
    >
      <div class="card-head">
        <h2 class="card-title">
          {{ CHROME.closeGaps }}
        </h2>
      </div>
      <div class="pills">
        <span
          v-for="item in closers"
          :key="item.id"
          class="pill"
        >+ {{ item.name }}<small>{{ fill(CHROME.closes, { n: item.closes }) }}</small></span>
      </div>
    </article>
  </section>
</template>

<style scoped>
/* the reference's kit grid (02-kit): the list over two rows, the kit aboard beside it, the planning card below.
   The hub serves no build note, so the kit aboard keeps both rows on the right. */
.f-kit .k-aboard {
  grid-row: 1 / span 2;
}
.go {
  color: inherit;
}
.k-close .pill {
  display: inline-flex;
  align-items: center;
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
