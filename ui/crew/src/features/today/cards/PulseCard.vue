<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import SourceWord from "../../../design/SourceWord.vue";
import SparkLine from "../../../design/charts/SparkLine.vue";
import { useCrewApi } from "../../../shell/api/client";
import RequestCard from "../../pulse/PulseCard.vue";
import { WORDS } from "../../pulse/words";
import { pulseReadings, type Observation } from "./pulseReadings";

/* After shot C the Today pulse card shows the recorded reading (value, unit, its time, quality and source word)
   and the line of this session's pulse readings ending on a glowing dot. Before any reading it is the pulse
   request card. Every value is the hub's observation. */
const api = useCrewApi();
const readings = ref<Observation[]>([]);
const latest = computed(() => readings.value[readings.value.length - 1] ?? null);
const values = computed(() => readings.value.map((r) => r.value));
const taken = computed(() => {
  const at = latest.value?.observed_at;
  return at ? new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
});

onMounted(async () => {
  readings.value = await pulseReadings(api).catch(() => []);
});
</script>

<template>
  <RequestCard v-if="latest === null" />
  <div
    v-else
    class="pulse-read"
    data-testid="pulse-reading"
  >
    <div class="card-head">
      <h2 class="card-title">
        {{ WORDS.pulseTitle }}
      </h2>
      <RouterLink
        class="go"
        :to="{ name: 'pulse' }"
        :aria-label="WORDS.pulseTitle"
      >
        <svg
          class="icon"
          viewBox="0 0 24 24"
        ><path d="M7 7h10v10" /><path d="M7 17 17 7" /></svg>
      </RouterLink>
    </div>
    <div class="big">
      {{ latest.original_value }}<small>{{ latest.original_unit }}</small>
    </div>
    <div class="pulse-line">
      <SparkLine
        :values="values"
        :range="null"
        :width="240"
        :height="64"
        :label="WORDS.pulseTitle"
      />
    </div>
    <p class="pulse-meta">
      <span>{{ WORDS.taken }} {{ taken }}</span>
      <span>{{ WORDS.quality }} {{ latest.quality_status }}</span>
      <SourceWord :origin="latest.origin_mode" />
    </p>
  </div>
</template>

<style scoped>
.pulse-read {
  display: flex;
  flex-direction: column;
}
.pulse-line {
  height: 64px;
  margin: 4px 0 8px;
  color: rgba(255, 255, 255, 0.92);
}
.pulse-line .spark {
  height: 100%;
}
.pulse-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 13px;
  line-height: 18px;
}
</style>
