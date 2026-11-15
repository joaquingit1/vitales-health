<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../../shell/session";

const session = useSession();
const who = computed(() => session.state.value.session);
</script>

<template>
  <main
    v-if="who"
    class="profile"
  >
    <h1 class="profile-name">
      {{ who.person?.display_name ?? who.actor.display_name }}
    </h1>
  </main>
</template>

<style scoped>
.profile {
  min-height: 100vh;
  padding: 80px 24px 24px;
  text-align: center;
}

.profile-name {
  font-size: 34px;
  line-height: 42px;
  font-weight: 300;
  letter-spacing: -0.01em;
}
</style>
