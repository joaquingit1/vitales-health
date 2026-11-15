<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";
import BodyFigure from "../design/BodyFigure.vue";
import { CHROME } from "../design/chrome";
import { copy } from "../design/copy";
import { useSession } from "./session";
import { useStage } from "./stage";

const stage = useStage();
const session = useSession();
const stageEl = ref<HTMLElement | null>(null);
const who = computed(() => {
  const current = session.state.value.session;
  return current?.person?.display_name ?? current?.actor.display_name ?? "";
});
const initials = computed(() =>
  who.value
    .split(" ")
    .filter((word) => word !== "")
    .map((word) => word[0] ?? "")
    .join("")
    .slice(0, 2),
);
const kitMode = computed(() => stage.marks.length > 0);
</script>

<template>
  <div
    class="app"
    data-testid="stage-layout"
  >
    <section
      ref="stageEl"
      class="stage"
      :class="{ 'kit-mode': kitMode }"
      aria-label="Body view"
    >
      <BodyFigure
        :marks="stage.marks"
        :focus="stage.focus"
        :stage-el="stageEl"
      />
      <header class="stage-head">
        <RouterLink
          class="brand-mark"
          :to="{ name: 'today' }"
          :aria-label="copy('display_group_labels.today')"
        >
          <svg
            viewBox="0 0 18 18"
            width="18"
            height="18"
            fill="none"
          ><path
            d="M13.6 3.4a7 7 0 1 1-9.2 0"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          /><circle
            cx="9"
            cy="2.6"
            r="1.5"
            fill="currentColor"
          /></svg>
        </RouterLink>
        <span class="brand">{{ CHROME.brand }}</span>
        <RouterLink
          class="urgent-btn"
          :to="{ name: 'help' }"
        >
          {{ CHROME.someoneNeedsHelp }}
        </RouterLink>
        <RouterLink
          class="who"
          :to="{ name: 'profile' }"
          :aria-label="who"
        >
          {{ initials }}
        </RouterLink>
      </header>
      <div class="title rest">
        <div
          class="who-line"
          data-testid="stage-subline"
        >
          {{ stage.subline }}
        </div>
        <h1 data-testid="stage-headline">
          {{ stage.headline }}
        </h1>
      </div>
    </section>
    <div class="side">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.brand-mark,
.who {
  text-decoration: none;
  color: inherit;
}
.who {
  display: grid;
  place-items: center;
}
.urgent-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}
</style>
