<script setup lang="ts">
import { inject, ref } from "vue";
import { useRouter } from "vue-router";
import { CHROME } from "../../design/chrome";
import { copy } from "../../design/copy";
import { ApiProblem } from "../../shell/api/client";
import { SESSION_LOCK, STATION_HEADING_ID } from "../identity/sessionLock";

const lock = inject(SESSION_LOCK);
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
      error instanceof ApiProblem && error.code !== null
        ? copy(`error_messages.${error.code}`).startsWith("error_messages.")
          ? `${CHROME.signInFailed}: ${error.code}`
          : copy(`error_messages.${error.code}`)
        : CHROME.signInFailed;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="station">
    <h1
      :id="STATION_HEADING_ID"
      class="station-heading"
      tabindex="-1"
    >
      {{ CHROME.brand }}
    </h1>
    <form
      class="card paper sign-in"
      data-testid="sign-in"
      @submit.prevent="submit"
    >
      <h2 class="card-title">
        {{ CHROME.signIn }}
      </h2>
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
  </main>
</template>

<style scoped>
.station {
  min-height: 100vh;
  padding: 80px 24px 24px;
  text-align: center;
}

.station-heading {
  font-size: 34px;
  line-height: 42px;
  font-weight: 300;
  letter-spacing: -0.01em;
}

.sign-in {
  width: 380px;
  margin: 40px auto 0;
  text-align: left;
  cursor: default;
}
.sign-in:hover {
  transform: none;
}
</style>
