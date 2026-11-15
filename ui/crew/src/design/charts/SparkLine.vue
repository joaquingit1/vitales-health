<script setup lang="ts">
import { computed } from "vue";
import { extent, polyline, smoothPath, type Range } from "./scale";

/* A short line that draws itself in (stroke-dashoffset) and ends on a glowing dot, as the reference's How you are
   and Habitat cards. It draws only the values it is given; an empty series renders nothing. */
const props = withDefaults(
  defineProps<{ values: readonly number[]; range?: Range | null; width?: number; height?: number; label: string }>(),
  { range: null, width: 160, height: 56 },
);

const domain = computed(() => props.range ?? extent(props.values));
const points = computed(() => (domain.value === null ? [] : polyline(props.values, props.width, props.height, domain.value)));
const d = computed(() => smoothPath(points.value));
const end = computed(() => points.value[points.value.length - 1] ?? null);
</script>

<template>
  <svg
    v-if="end"
    class="spark"
    :viewBox="`0 0 ${props.width} ${props.height}`"
    preserveAspectRatio="none"
    role="img"
    :aria-label="props.label"
    data-testid="sparkline"
  >
    <path
      class="spark-line"
      :d="d"
      pathLength="1"
    />
    <circle
      class="spark-glow"
      :cx="end.x"
      :cy="end.y"
      r="6"
    />
    <circle
      class="spark-dot"
      :cx="end.x"
      :cy="end.y"
      r="3"
    />
  </svg>
</template>

<style scoped>
.spark {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}
.spark-line {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  animation: spark-draw 1.4s var(--ease, ease-out) 0.15s forwards;
}
.spark-dot {
  fill: var(--lime, #e2f55c);
  opacity: 0;
  animation: spark-in 0.4s ease-out 1.4s forwards;
}
.spark-glow {
  fill: var(--lime, #e2f55c);
  opacity: 0;
  filter: blur(3px);
  animation: spark-glow 0.6s ease-out 1.4s forwards;
}
@keyframes spark-draw {
  to {
    stroke-dashoffset: 0;
  }
}
@keyframes spark-in {
  to {
    opacity: 1;
  }
}
@keyframes spark-glow {
  to {
    opacity: 0.55;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spark-line {
    animation: none;
    stroke-dashoffset: 0;
  }
  .spark-dot {
    animation: none;
    opacity: 1;
  }
  .spark-glow {
    animation: none;
    opacity: 0.55;
  }
}
</style>
