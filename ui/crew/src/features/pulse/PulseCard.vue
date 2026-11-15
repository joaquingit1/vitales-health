<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useCrewApi } from "../../shell/api/client";
import { copy } from "../../design/copy";
import { refusal, type Capability } from "./hub";
import { WORDS } from "./words";

const api = useCrewApi();
const router = useRouter();
const pulse = ref<Capability | null>(null);
const problem = ref("");

onMounted(async () => {
  try {
    const caps = await api.get<{ items: Capability[] }>("/v1/capabilities");
    pulse.value = caps.items.find((c) => c.code === "pulse") ?? null;
  } catch (failure) {
    problem.value = refusal(failure);
  }
});
</script>

<template>
  <div
    class="pulse-card"
    data-test="pulse-card"
  >
    <h2 class="card-title">
      {{ WORDS.pulseTitle }}
    </h2>
    <p
      v-if="pulse"
      class="sub"
    >
      {{ pulse.label }}<template v-if="pulse.expected_duration_seconds">
        · {{ copy("guided_check_words.seconds_left", { n: pulse.expected_duration_seconds }) }}
      </template>
    </p>
    <p
      v-else-if="problem"
      class="sub"
    >
      {{ problem }}
    </p>
    <button
      class="pill do"
      type="button"
      :disabled="!pulse || pulse.status !== 'available'"
      @click="router.push({ name: 'pulse', query: router.currentRoute.value.query })"
    >
      {{ WORDS.cardTake }}
    </button>
  </div>
</template>

<style scoped>
/* the shared parts (app.css .card-title, .sub, .pill.do) dress it, on paper or on a gradient */
.pulse-card { display: flex; flex-direction: column; gap: 8px; }
.pill { align-self: flex-start; }
</style>
