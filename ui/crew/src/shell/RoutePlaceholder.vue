<script setup lang="ts">
import { useRoute } from "vue-router";

const route = useRoute();
</script>

<template>
  <main class="route-placeholder">
    {{ String(route.name ?? "") }}
  </main>
</template>
