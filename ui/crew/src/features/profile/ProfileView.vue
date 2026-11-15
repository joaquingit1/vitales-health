<script setup lang="ts">
import { computed, inject } from "vue";
import { RouterLink } from "vue-router";
import { copy } from "../../design/copy";
import { SESSION_LOCK } from "../identity/sessionLock";
import { useCrewApi } from "../../shell/api/client";
import ContextBar from "../../shell/context/ContextBar.vue";
import { useSession } from "../../shell/session";

const session = useSession();
const who = computed(() => session.state.value.session);
const api = useCrewApi();
const lock = inject(SESSION_LOCK, null);
const name = computed(() => who.value?.person?.display_name ?? who.value?.actor.display_name ?? "");
const read = <T,>(path: string): Promise<T> => api.get<T>(path);
</script>

<template>
  <ContextBar
    :read="read"
    :who="name"
    home-to="/today"
    help-to="/help"
    @sign-out="lock?.signOut()"
  />
  <main
    v-if="who"
    class="profile"
  >
    <h1 class="profile-name">
      {{ name }}
    </h1>
    <RouterLink
      :to="{ name: 'today' }"
      class="profile-back"
    >
      {{ copy("display_group_labels.today") }}
    </RouterLink>
  </main>
</template>

<style scoped>
.profile {
  padding: 80px 24px 24px;
  text-align: center;
}
.profile-back { display: inline-block; margin-top: 24px; color: var(--green); }

.profile-name {
  font-size: 34px;
  line-height: 42px;
  font-weight: 300;
  letter-spacing: -0.01em;
}
</style>
