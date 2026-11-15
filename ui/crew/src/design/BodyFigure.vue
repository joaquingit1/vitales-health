<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { bodyRender, type BodyHandle, type FigureMark } from "./body";

const props = defineProps<{ marks: readonly FigureMark[]; focus?: string | null; stageEl?: HTMLElement | null; whole?: boolean }>();

/* Only a region the figure has a point for gets a mark ("whole_body" has none): an unmapped mark is skipped,
   never drawn at the canvas origin. */
const shown = computed(() => {
  const api = bodyRender();
  return api === null ? [] : props.marks.filter((mark) => api.point(mark.region) !== null);
});

const canvas = ref<HTMLCanvasElement | null>(null);
const spots = ref<HTMLElement[]>([]);
const failed = ref(false);
let body: BodyHandle | null = null;
let observer: ResizeObserver | null = null;

function place(): void {
  const api = bodyRender();
  if (api === null) {
    return;
  }
  for (const el of spots.value) {
    const region = el.dataset["at"];
    if (region === undefined) {
      continue;
    }
    const at = api.point(region);
    if (at === null) {
      continue;
    }
    const p = api.project(at);
    el.style.transform = `translate(${p.x.toFixed(1)}px,${p.y.toFixed(1)}px)`;
  }
}

function frame(): { top: number; bottom: number } {
  const host = props.stageEl ?? canvas.value?.parentElement ?? null;
  if (host === null) {
    return { top: 182, bottom: 150 };
  }
  const style = getComputedStyle(host);
  return {
    top: parseFloat(style.getPropertyValue("--fig-top")) || 182,
    bottom: parseFloat(style.getPropertyValue("--fig-bottom")) || 150,
  };
}

onMounted(() => {
  const api = bodyRender();
  if (api === null || canvas.value === null) {
    failed.value = true;
    return;
  }
  try {
    body = api.mount(canvas.value, { breathsPerMin: 14, onFrame: place, frame, whole: props.whole === true });
    body.start();
    observer = new ResizeObserver(() => body?.resize());
    observer.observe(canvas.value);
  } catch (error) {
    console.error(error);
    failed.value = true;
  }
});

onBeforeUnmount(() => {
  body?.stop();
  observer?.disconnect();
});

watch(
  () => props.focus,
  (name) => {
    body?.focus(name ?? null, { camera: name != null, ax: 0.5 });
  },
);
watch(() => props.marks, () => requestAnimationFrame(place), { deep: true });
</script>

<template>
  <canvas
    id="body"
    ref="canvas"
    aria-hidden="true"
  />
  <template v-if="!failed">
    <i
      v-for="(mark, index) in shown"
      :key="`${mark.region}-${mark.kind}-${index}`"
      ref="spots"
      class="spot rest kit-spot"
      :class="{ hollow: mark.kind === 'hollow', open: mark.kind === 'open' }"
      :data-at="mark.region"
    />
  </template>
</template>
