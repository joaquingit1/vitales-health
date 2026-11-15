<script setup lang="ts">
import { RouterLink } from "vue-router";
import "./frame.css";

defineProps<{ whoLine: string; title: string; urgentLabel: string | null }>();
</script>

<template>
  <div class="ph-app">
    <section class="ph-stage">
      <header class="ph-head">
        <span
          class="ph-brand-mark"
          aria-hidden="true"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          ><path d="M21 12a9 9 0 1 1-6.2-8.56" /></svg>
        </span>
        <span class="ph-brand"><slot name="brand" /></span>
        <RouterLink
          v-if="urgentLabel"
          class="ph-urgent"
          :to="{ name: 'help' }"
        >
          {{ urgentLabel }}
        </RouterLink>
      </header>
      <div class="ph-title">
        <p class="who-line">
          {{ whoLine }}
        </p>
        <h1>{{ title }}</h1>
        <slot name="under-title" />
      </div>
      <div class="ph-foot">
        <slot name="foot" />
      </div>
    </section>
    <section class="ph-cards">
      <slot />
    </section>
  </div>
</template>
