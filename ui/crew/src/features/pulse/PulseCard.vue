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
.pulse-card { display: flex; flex-direction: column; gap: 8px; }
.card-title { font-size: 17px; line-height: 22px; font-weight: 500; }
.sub { font-size: 15px; line-height: 22px; color: var(--ink-2); }
.pill { align-self: flex-start; height: 30px; padding: 0 14px; border-radius: 15px; border: 1px solid var(--ink); background: var(--ink); color: #fff; font-size: 13px; cursor: pointer; }
.pill[disabled] { opacity: 0.4; cursor: not-allowed; }
</style>
