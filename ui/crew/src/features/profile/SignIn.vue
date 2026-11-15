<script setup lang="ts">
import { inject, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { CHROME } from "../../design/chrome";
import { copy } from "../../design/copy";
import { ApiProblem } from "../../shell/api/client";
import { SESSION_LOCK, STATION_HEADING_ID } from "../identity/sessionLock";
import { useStage } from "../../shell/stage";

const lock = inject(SESSION_LOCK);
const stage = useStage();
onMounted(() => {
  stage.marks = [];
  stage.focus = null;
  stage.subline = "";
  stage.headline = CHROME.brand;
});
const router = useRouter();
const person = ref("");
const credential = ref("");
const busy = ref(false);
const failure = ref("");

async function submit(): Promise<void> {
  if (lock === undefined || busy.value) {
    return;
  }
  busy.value = true;
  failure.value = "";
  try {
    await lock.signIn({ kind: "crew", person_id: person.value.trim(), credential: credential.value });
    credential.value = "";
    await router.push({ name: "today" });
  } catch (error) {
    failure.value =
      error instanceof ApiProblem && error.code !== null && !copy(`error_messages.${error.code}`).startsWith("error_messages.")
        ? copy(`error_messages.${error.code}`)
        : CHROME.signInFailed;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section
    class="cards flow station"
    aria-label="Sign in"
  >
    <form
      class="card paper sign-in"
      data-testid="sign-in"
      @submit.prevent="submit"
    >
      <h1
        :id="STATION_HEADING_ID"
        class="card-title"
        tabindex="-1"
      >
        {{ CHROME.signIn }}
      </h1>
      <input
        v-model="person"
        class="line-in"
        name="person"
        autocomplete="username"
        :placeholder="CHROME.person"
        :aria-label="CHROME.person"
      >
      <input
        v-model="credential"
        class="line-in"
        name="credential"
        type="password"
        autocomplete="current-password"
        :placeholder="CHROME.credential"
        :aria-label="CHROME.credential"
      >
      <p
        v-if="failure"
        class="fine"
        role="alert"
      >
        {{ failure }}
      </p>
      <div class="pills">
        <button
          class="pill do"
          type="submit"
          :disabled="busy || person.trim() === '' || credential === ''"
        >
          {{ CHROME.signIn }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
/* the station sits on the stage like every screen (UX-DESIGN 1.2): the figure on the left, the form card on the
   reference grid's first row */
.sign-in {
  grid-column: 1 / span 14;
  grid-row: 1;
}
.sign-in .pills {
  margin-top: auto;
}
</style>
