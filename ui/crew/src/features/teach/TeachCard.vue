<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useCrewApi } from "../../shell/api/client";
import { useSession } from "../../shell/session";
import type { GuidedCheck, TeachSteps } from "../pulse/hub";
import { WORDS } from "../pulse/words";
import { HANDOFF_KEY } from "./handoff";

const api = useCrewApi();
const session = useSession();
const router = useRouter();
const title = ref("");
const id = ref<string | null>(null);

/** The check this person helps with, when the hub lets them read it; nothing otherwise. */
onMounted(async () => {
  let stored: string | null = null;
  try {
    stored = window.sessionStorage.getItem(HANDOFF_KEY);
  } catch {
    stored = null;
  }
  if (!stored) return;
  try {
    const check = await api.get<GuidedCheck>(`/v1/guided-checks/${stored}`);
    if (check.helper.person_id !== session.current?.person?.person_id || check.state === "cancelled") return;
    const steps = await api.get<TeachSteps>(`/v1/guided-checks/${stored}/steps`);
    title.value = steps.card_title_text;
    id.value = stored;
  } catch {
    id.value = null;
  }
});
</script>

<template>
  <div
    v-if="id"
    class="teach-card"
    data-test="teach-card"
  >
    <h2 class="card-title">
      {{ title }}
    </h2>
    <button
      class="pill do"
      type="button"
      @click="router.push({ name: 'teach', params: { id } })"
    >
      {{ WORDS.continueCheck }}
    </button>
  </div>
</template>

<style scoped>
.teach-card { display: flex; flex-direction: column; gap: 8px; }
.card-title { font-size: 17px; line-height: 22px; font-weight: 500; }
.pill { align-self: flex-start; }
</style>
