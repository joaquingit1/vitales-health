<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { CHROME } from "../../design/chrome";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import { inTime } from "../../features/today/groundLink";
import { environmentIcon, linkSimulated, readMissionContext, type MissionContext, type Read } from "./missionContext";

/**
 * The context bar (E03 7.16): on every screen, signed in or not. Where the crew is (environment and its icon),
 * when (mission day and MET), whether Earth can hear them (ground link, one-way delay, next contact) and, when
 * anything on screen is not live, a "Simulated inputs" marker that says which inputs are simulated.
 */
const props = defineProps<{ read: Read; who?: string; homeTo?: string; helpTo?: string | null }>();
const emit = defineEmits<{ help: []; signOut: [] }>();
const context = ref<MissionContext | null>(null);
const failed = ref(false);
const simCount = ref(0);
const replayCount = ref(0);
const open = ref(false);
const POLL_MS = 2000;
let timer = 0;

/* Every value carries its source word (SourceWord renders data-origin); the bar counts what is on screen now. */
function scan(): void {
  const count = (origin: string): number =>
    Array.from(document.querySelectorAll(`[data-origin="${origin}"]`)).filter((el) => el.closest(".ctx") === null).length;
  simCount.value = count("SIM");
  replayCount.value = count("REPLAY");
}

async function refresh(): Promise<void> {
  try {
    context.value = await readMissionContext(props.read);
    failed.value = false;
  } catch {
    failed.value = true;
  }
  scan();
}

onMounted(() => {
  void refresh();
  timer = window.setInterval(() => void refresh(), POLL_MS);
});
onBeforeUnmount(() => {
  window.clearInterval(timer);
});

const icon = computed(() => environmentIcon(context.value?.environmentId ?? null));
const link = computed(() => context.value?.link ?? null);
const missionSim = computed(() => context.value?.missionMode != null && context.value.missionMode !== "LIVE");
const linkSim = computed(() => linkSimulated(link.value));
const simulated = computed(
  () => simCount.value > 0 || replayCount.value > 0 || linkSim.value || missionSim.value,
);
const delay = computed(() => {
  const seconds = link.value?.one_way_delay_s;
  if (seconds == null) {
    return null;
  }
  const words =
    seconds >= 120
      ? copy("duration_words.minutes", { n: Math.floor(seconds / 60) })
      : copy("duration_words.seconds", { n: Math.round(seconds) });
  return copy("context_words.one_way_delay", { delay: words });
});
const nextContact = computed(() => {
  const at = link.value?.next_contact_at;
  if (link.value === null || at == null || link.value.status === "up") {
    return null;
  }
  const words = inTime(context.value?.serverTime ?? Number.NaN, at);
  return words === null ? null : copy("designed_states.ground_next_contact_in", { in_time: words });
});
const initials = computed(() =>
  (props.who ?? "")
    .split(" ")
    .filter((word) => word !== "")
    .map((word) => word[0] ?? "")
    .join("")
    .slice(0, 2),
);
const reasons = computed(() => {
  const lines: string[] = [];
  if (missionSim.value) {
    lines.push(copy("context_words.sim_mission"));
  }
  if (linkSim.value) {
    lines.push(copy("context_words.sim_ground_link"));
  }
  if (simCount.value > 0) {
    lines.push(copy("context_words.sim_values", { n: simCount.value }));
  }
  if (replayCount.value > 0) {
    lines.push(copy("context_words.replay_values", { n: replayCount.value }));
  }
  return lines;
});
defineExpose({ refresh });
</script>

<template>
  <header
    class="ctx"
    data-testid="context-bar"
    :data-environment="context?.environmentId ?? ''"
    :data-link="link?.status ?? ''"
  >
    <RouterLink
      class="ctx-brand"
      :to="props.homeTo ?? '/'"
    >
      <span class="brand-mark"><svg
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
      /></svg></span>
      <span class="brand">{{ CHROME.brand }}</span>
    </RouterLink>

    <div
      class="ctx-item env"
      data-testid="ctx-environment"
      :title="copy('context_words.environment_label')"
    >
      <svg
        class="env-icon"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <template v-if="icon === 'station'"><rect
          x="9"
          y="9"
          width="6"
          height="6"
          rx="1"
        /><path d="M2 8h5v8H2zM17 8h5v8h-5zM7 12h2M15 12h2" /></template>
        <template v-else-if="icon === 'transit'"><path d="M5 19c2-6 6-11 14-14-3 8-8 12-14 14z" /><circle
          cx="14"
          cy="10"
          r="1.6"
        /><path d="M5 19l3-1M5 19l1-3" /></template>
        <template v-else-if="icon === 'moon'"><path d="M3 18h18" /><path d="M6 18v-3h5v3M8.5 15v-2" /><path d="M18 4a4 4 0 1 0 2.5 6.6A4.5 4.5 0 0 1 18 4z" /></template>
        <template v-else-if="icon === 'mars'"><circle
          cx="12"
          cy="12"
          r="8"
        /><path d="M6 9c3 1 5 0 8-2M8 16c2-1 5-1 9 0" /></template>
        <template v-else><circle
          cx="12"
          cy="12"
          r="8"
        /></template>
      </svg>
      <span class="env-text">
        <b>{{ context?.environmentLabel ?? copy("designed_states.unknown") }}</b>
        <small v-if="context?.place">{{ context.place }}</small>
      </span>
    </div>

    <div
      v-if="context?.missionDay != null"
      class="ctx-item ctx-clock"
      data-testid="ctx-clock"
    >
      <b>{{ copy("context_words.mission_day", { n: context.missionDay }) }}</b>
      <small v-if="context?.met">{{ copy("context_words.met", { met: context.met }) }}</small>
    </div>

    <div
      class="ctx-item link"
      :class="`link-${link?.status ?? 'unknown'}`"
      data-testid="ctx-link"
    >
      <i class="dot" />
      <span class="env-text">
        <b>{{ link ? copy(`ground_link_words.${link.status}`) : copy("designed_states.unknown") }}</b>
        <small v-if="link">
          <template v-if="nextContact">{{ nextContact }}</template>
          <template v-if="nextContact && delay"> · </template>
          <template v-if="delay">{{ delay }}</template>
        </small>
      </span>
      <SourceWord
        v-if="link"
        :origin="linkSim ? 'SIM' : 'LIVE'"
      />
    </div>

    <div class="ctx-sim-wrap">
      <button
        v-if="simulated"
        type="button"
        class="ctx-sim"
        data-testid="ctx-simulated"
        :aria-expanded="open"
        @click="open = !open"
      >
        <i aria-hidden="true" />{{ copy("context_words.simulated_inputs") }}
      </button>
      <div
        v-if="simulated && open"
        class="ctx-pop"
        role="dialog"
        :aria-label="copy('context_words.simulated_title')"
        data-testid="ctx-simulated-detail"
      >
        <p class="pop-title">
          {{ copy("context_words.simulated_title") }}
        </p>
        <ul>
          <li
            v-for="line in reasons"
            :key="line"
          >
            {{ line }}
          </li>
        </ul>
      </div>
    </div>

    <RouterLink
      v-if="props.helpTo !== null && props.helpTo !== undefined"
      class="urgent-btn"
      :to="props.helpTo"
    >
      {{ CHROME.someoneNeedsHelp }}
    </RouterLink>
    <button
      v-else
      type="button"
      class="urgent-btn"
      data-testid="ctx-help"
      @click="emit('help')"
    >
      {{ CHROME.someoneNeedsHelp }}
    </button>
    <RouterLink
      v-if="initials !== ''"
      class="who"
      :to="{ name: 'profile' }"
      :aria-label="props.who"
    >
      {{ initials }}
    </RouterLink>
    <button
      v-if="initials !== ''"
      type="button"
      class="pill"
      data-testid="sign-out"
      @click="emit('signOut')"
    >
      {{ copy("context_words.sign_out") }}
    </button>
  </header>
</template>

<style scoped>
.ctx { position: relative; z-index: 6; display: flex; align-items: center; gap: 10px; padding: 14px 20px; min-height: 68px; border-bottom: 1px solid var(--line); background: rgba(252, 252, 250, 0.82); backdrop-filter: blur(8px); }
.ctx-brand { display: flex; align-items: center; gap: 10px; color: inherit; text-decoration: none; margin-right: 8px; }
.ctx-item { display: flex; align-items: center; gap: 10px; padding: 6px 14px; border-radius: 14px; background: var(--paper); box-shadow: inset 0 0 0 1px var(--line); min-height: 44px; }
.ctx-item b { display: block; font-weight: 500; font-size: 14px; line-height: 18px; white-space: nowrap; }
.ctx-item small { display: block; font-size: 12px; line-height: 16px; color: var(--ink-3); white-space: nowrap; }
.env-icon { width: 22px; height: 22px; fill: none; stroke: var(--green); stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; flex: none; }
.ctx-clock small { font-family: var(--mono); }
.link .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ink-3); flex: none; }
.link-up .dot { background: #3f8f5c; }
.link-delayed .dot { background: #c98a1b; }
.link-down .dot { background: #b0442b; box-shadow: 0 0 0 4px rgba(176, 68, 43, 0.16); }
.link-down { background: rgba(176, 68, 43, 0.07); box-shadow: inset 0 0 0 1px rgba(176, 68, 43, 0.3); }
.ctx-sim-wrap { position: relative; }
.ctx-sim { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 14px; border: 1px dashed rgba(28, 30, 28, 0.45); border-radius: 18px; background: rgba(28, 30, 28, 0.04); font-size: 13px; font-weight: 500; }
.ctx-sim i { width: 12px; height: 12px; border-radius: 3px; background: repeating-linear-gradient(135deg, #8f9b90 0 1.4px, rgba(143, 155, 144, 0) 1.4px 4px); box-shadow: inset 0 0 0 1px rgba(143, 155, 144, 0.6); }
.ctx-pop { position: absolute; top: 44px; left: 0; width: 360px; padding: 14px 16px; border-radius: 16px; background: var(--paper); box-shadow: 0 12px 32px rgba(28, 30, 28, 0.16); font-size: 13px; line-height: 18px; }
.ctx-pop ul { margin: 8px 0 0; padding-left: 18px; color: var(--ink-2); }
.ctx-pop li + li { margin-top: 6px; }
.pop-title { font-weight: 500; }
.urgent-btn { margin-left: auto; display: inline-flex; align-items: center; text-decoration: none; }
.who { display: grid; place-items: center; color: inherit; text-decoration: none; }
@media (max-width: 1100px) {
  .ctx { flex-wrap: wrap; }
}
</style>
