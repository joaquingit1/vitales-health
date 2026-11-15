<script setup lang="ts">
import { computed } from "vue";
import { fraction, type Range } from "./scale";

/* The reference's channel bar: a track over the hub's display range, a fill to the current value and a dotted tick
   at each limit mark. With no value the track and the limits still show; nothing is filled. */
const props = defineProps<{ value: number | null; range: Range; limits: readonly number[]; level?: string | null }>();

const fill = computed(() => (props.value === null ? null : fraction(props.value, props.range) * 100));
const ticks = computed(() => props.limits.map((v) => fraction(v, props.range) * 100));
</script>

<template>
  <div
    class="lbar"
    data-testid="limit-bar"
  >
    <span
      v-if="fill !== null"
      class="lbar-fill"
      :class="props.level ? `lv-${props.level}` : ''"
      :style="{ width: `${fill}%` }"
    />
    <span
      v-for="(at, i) in ticks"
      :key="i"
      class="lbar-limit"
      :style="{ left: `${at}%` }"
      data-testid="limit-mark"
    />
  </div>
</template>

<style scoped>
.lbar {
  position: relative;
  height: 3px;
  margin-top: 8px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.28);
}
.lbar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 2px;
  background: var(--lime, #e2f55c);
  transform-origin: left center;
  animation: lbar-grow 1s var(--ease, ease-out) both;
}
.lbar-fill:not([class*="lv-"]),
.lbar-fill.lv-info {
  background: rgba(255, 255, 255, 0.9);
}
.lbar-limit {
  position: absolute;
  top: -4px;
  bottom: -4px;
  width: 0;
  border-left: 2px dotted rgba(255, 255, 255, 0.95);
}
@keyframes lbar-grow {
  from {
    transform: scaleX(0);
  }
}
@media (prefers-reduced-motion: reduce) {
  .lbar-fill {
    animation: none;
  }
}
</style>
