<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { copy } from "../../../design/copy";
import LimitBar from "../../../design/charts/LimitBar.vue";
import SparkLine from "../../../design/charts/SparkLine.vue";
import { useCrewApi } from "../../../shell/api/client";
import { TILE_MAX, seriesOf, sourcesOf, tileOf, type ChannelTile, type SensorStatus } from "./habitat";

/* The Habitat card's channel tiles (reference 01-today.png): value, unit, name, a bar over the hub's display range
   with a dotted tick at each limit, a short line of the accepted series, and each value's source words. Everything
   comes from listSensorStatus and getSensorSeries; a sensor with no accepted reading shows the hub's no reading
   text and an empty bar. */
const api = useCrewApi();
const tiles = ref<ChannelTile[]>([]);
const failed = ref(false);
const POLL_MS = 15000;
let poll = 0;

async function load(): Promise<void> {
  try {
    const list = await api.get<{ items: SensorStatus[] }>("/v1/sensors");
    const shown = list.items.slice(0, TILE_MAX);
    const lines = await Promise.all(shown.map((status) => seriesOf(api, status).catch(() => [])));
    tiles.value = shown.map((status, index) => ({ ...tileOf(status), series: lines[index] ?? [] }));
    failed.value = false;
  } catch {
    failed.value = tiles.value.length === 0;
  }
}

onMounted(() => {
  void load();
  poll = window.setInterval(() => void load(), POLL_MS);
});
onBeforeUnmount(() => window.clearInterval(poll));
</script>

<template>
  <div
    v-if="tiles.length > 0"
    class="hab-tiles"
    data-testid="habitat-tiles"
  >
    <div class="hab-row">
      <div
        v-for="tile in tiles"
        :key="tile.id"
        class="hab-tile"
        :data-sensor="tile.id"
        :data-state="tile.current ? 'current' : 'no_reading'"
      >
        <div class="hab-spark">
          <SparkLine
            v-if="tile.series.length > 1"
            :values="tile.series"
            :range="null"
            :width="120"
            :height="34"
            :label="tile.name"
          />
        </div>
        <div
          class="hab-value"
          :class="{ word: tile.value === null }"
        >
          {{ tile.text }}<small v-if="tile.unit">{{ tile.unit }}</small>
        </div>
        <div class="hab-name">
          <i
            v-if="tile.level && tile.level !== 'info'"
            class="hab-dot"
          />{{ tile.name }}
        </div>
        <LimitBar
          :value="tile.value"
          :range="tile.range"
          :limits="tile.limits"
          :level="tile.level"
          :title="tile.limitLabels.join(', ')"
        />
      </div>
    </div>
    <div class="swatches hab-legend">
      <span
        v-for="source in sourcesOf(tiles)"
        :key="source.label"
        :class="`s-${source.origin.toLowerCase()}`"
        :data-origin="source.origin"
      ><i aria-hidden="true" />{{ source.label }}</span>
      <span
        v-if="tiles.some((t) => t.limits.length > 0)"
        class="s-limit"
      ><i aria-hidden="true" />{{ tiles.find((t) => t.limitLabels.length > 0)?.limitLabels[0] }}</span>
    </div>
  </div>
  <p
    v-else-if="failed"
    class="hab-state"
  >
    {{ copy("designed_states.unknown") }}
  </p>
</template>

<style scoped>
.hab-tiles {
  margin-top: auto;
  padding-top: 16px;
}
.hab-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.hab-spark {
  height: 34px;
  margin-bottom: 6px;
  color: rgba(255, 255, 255, 0.92);
}
.hab-value {
  font-size: 26px;
  line-height: 30px;
  font-weight: 300;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.hab-value small {
  font-size: 12px;
  margin-left: 4px;
}
.hab-value.word {
  font-size: 15px;
  line-height: 30px;
  font-weight: 400;
  opacity: 0.85;
}
.hab-name {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 13px;
  line-height: 16px;
}
.hab-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--lime, #e2f55c);
  box-shadow: 0 0 6px var(--lime, #e2f55c);
}
.hab-legend {
  flex-wrap: wrap;
  margin-top: 14px;
}
.hab-legend .s-limit i {
  border-top: 2px dotted rgba(255, 255, 255, 0.95);
  background: none;
}
.hab-state {
  margin-top: auto;
}
</style>
