<script setup lang="ts">
import { computed } from "vue";
import { originWord } from "./copy";

const props = defineProps<{ origin: string }>();
const word = computed(() => originWord(props.origin));
</script>

<template>
  <span
    class="src"
    :data-origin="props.origin.toUpperCase()"
  >{{ word }}</span>
</template>
