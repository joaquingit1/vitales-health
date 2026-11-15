<script setup lang="ts">
import { provide } from "vue";
import { RouterView } from "vue-router";
import { useSession } from "./session";
import StageLayout from "./StageLayout.vue";
import { STAGE, createStage } from "./stage";

const state = useSession().state;
provide(STAGE, createStage());
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <template v-if="Component && (route.meta.private !== true || state.session !== null)">
      <StageLayout
        v-if="(route.meta.private === true && route.meta.stage !== false) || route.meta.stage === true"
        :key="state.epoch"
      >
        <component :is="Component" />
      </StageLayout>
      <component
        :is="Component"
        v-else
        :key="state.epoch"
      />
    </template>
  </RouterView>
</template>

<style>
* {
  box-sizing: border-box;
}

html,
body {
  height: 100%;
}

body {
  margin: 0;
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 20px;
  -webkit-font-smoothing: antialiased;
  background-color: var(--page);
  background-image: radial-gradient(rgba(28, 30, 28, 0.065) 1px, transparent 1.2px);
  background-size: 14px 14px;
}

h1,
h2,
p {
  margin: 0;
  font-weight: 400;
}

button,
input {
  font: inherit;
  color: inherit;
}

[hidden] {
  display: none !important;
}

:focus-visible {
  outline: 2px solid var(--green-2);
  outline-offset: 2px;
}
</style>
