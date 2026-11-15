<script setup lang="ts">
import { STATION_HEADING_ID } from "../identity/sessionLock";
</script>

<template>
  <main class="station">
    <h1
      :id="STATION_HEADING_ID"
      class="station-heading"
      tabindex="-1"
    />
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
</style>
