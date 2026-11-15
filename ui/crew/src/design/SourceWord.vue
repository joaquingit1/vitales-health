<script setup lang="ts">
import { computed } from "vue";
import { originWord } from "./copy";

const props = defineProps<{ origin: string }>();
const word = computed(() => originWord(props.origin));
/* The reference's swatch language: a small square in the source's fill, then the word (interaction-prototype.html .swatches). */
const kind = computed(() => `s-${props.origin.toLowerCase()}`);
</script>

<template>
  <span
    class="src"
    :class="kind"
    :data-origin="props.origin.toUpperCase()"
  ><i aria-hidden="true" />{{ word }}</span>
</template>
