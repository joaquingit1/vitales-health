<script setup lang="ts">
import type { components } from "@crew-health/contracts";
import { computed, inject, nextTick, onMounted, provide, ref, type Component } from "vue";
import { useRouter } from "vue-router";
import { CHROME } from "../../design/chrome";
import { copy } from "../../design/copy";
import SourceWord from "../../design/SourceWord.vue";
import { ApiProblem, CREW_API, type CrewApi } from "../../shell/api/client";
import ContextBar from "../../shell/context/ContextBar.vue";
import { linkSimulated, type GroundLink } from "../../shell/context/missionContext";
import { SESSION_LOCK, STATION_HEADING_ID } from "../identity/sessionLock";
import { StationUnpaired, useStation } from "../identity/station";
import { KIT_STATES, STATE_MK, type KitMapView } from "../kit/kitMap";

/**
 * The home screen: the station view on the shared screen (E03 StationHome, E02 station principal). It reads only
 * what the contract serves to role station: the roster (listCrew, no health or participation fields), the mission
 * context, the ground link, the kit map and the assistant state. Choosing a crew member and entering their PIN
 * opens that person's Today; signing out returns here.
 */
type CrewMember = components["schemas"]["crew-member.schema"];
type AssistantStatus = { state: string };

const lock = inject(SESSION_LOCK);
const station = useStation();
const router = useRouter();

const paired = ref(station?.paired ?? false);
const pairing = ref("");
const pairFailed = ref(false);
const crew = ref<CrewMember[] | null>(null);
const rosterFailed = ref(false);
const kit = ref<KitMapView | null>(null);
const link = ref<GroundLink | null>(null);
const assistant = ref<string | null>(null);

const chosen = ref<string | null>(null);
const intent = ref<"today" | "help">("today");
const credential = ref("");
const busy = ref(false);
const failure = ref("");
const pinEl = ref<HTMLInputElement[] | HTMLInputElement | null>(null);

const read = <T,>(path: string): Promise<T> =>
  station === null ? Promise.reject(new StationUnpaired()) : station.get<T>(path);

/* The Today Habitat card's channel tiles (features/today/cards/HabitatCard.vue) read listSensorStatus and
   getSensorSeries, which role station may read: on this screen they read through the station session. Only
   `get` exists here, so nothing the station may not do can be sent from inside them. */
const cardFiles = import.meta.glob<Component>("../today/cards/HabitatCard.vue", { eager: true, import: "default" });
const habitatCard = cardFiles["../today/cards/HabitatCard.vue"] ?? null;
provide(CREW_API, { get: <T,>(path: string) => read<T>(path) } as unknown as CrewApi);

async function load(): Promise<void> {
  if (station === null || !station.paired) {
    paired.value = false;
    return;
  }
  const roster = station.get<{ items: CrewMember[] }>("/v1/crew").then(
    (value) => {
      crew.value = value.items;
      rosterFailed.value = false;
    },
    (error: unknown) => {
      rosterFailed.value = true;
      if (error instanceof StationUnpaired) {
        paired.value = false;
      }
    },
  );
  const kitRead = station.get<KitMapView>("/v1/kit-map").then(
    (value) => (kit.value = value),
    () => (kit.value = null),
  );
  const linkRead = station.get<GroundLink>("/v1/ground-link").then(
    (value) => (link.value = value),
    () => (link.value = null),
  );
  const assistantRead = station.get<AssistantStatus>("/v1/assistant/status").then(
    (value) => (assistant.value = value.state),
    () => (assistant.value = null),
  );
  await Promise.all([roster, kitRead, linkRead, assistantRead]);
}

async function pair(): Promise<void> {
  if (station === null || pairing.value === "") {
    return;
  }
  pairFailed.value = false;
  try {
    await station.pair(pairing.value);
    pairing.value = "";
    paired.value = true;
    await load();
  } catch {
    pairFailed.value = true;
  }
}

onMounted(() => {
  void load();
});

function roleWord(member: CrewMember): string {
  if (member.roles.includes("crew_medical_officer")) {
    return copy("review_role_words.crew_medical_officer");
  }
  return copy("context_words.role_crew");
}

function statusWord(member: CrewMember): string {
  if (member.helping_session_id != null) {
    return copy("context_words.status_helping");
  }
  const overlay = member.active_overlays[0];
  if (overlay !== undefined) {
    const word = copy(`overlay_words.${overlay}`);
    return word.startsWith("overlay_words.") ? overlay : word;
  }
  return member.location ?? copy("context_words.status_unknown");
}

function initials(name: string): string {
  return name
    .split(" ")
    .filter((word) => word !== "")
    .map((word) => word[0] ?? "")
    .join("")
    .slice(0, 2);
}

async function choose(member: CrewMember): Promise<void> {
  chosen.value = chosen.value === member.person_id ? null : member.person_id;
  credential.value = "";
  failure.value = "";
  await nextTick();
  const el = Array.isArray(pinEl.value) ? pinEl.value[0] : pinEl.value;
  el?.focus();
}

function askHelp(): void {
  intent.value = "help";
  chosen.value = null;
  document.getElementById("roster")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submit(): Promise<void> {
  if (lock === undefined || busy.value || chosen.value === null) {
    return;
  }
  busy.value = true;
  failure.value = "";
  try {
    await lock.signIn({ kind: "crew", person_id: chosen.value, credential: credential.value });
    credential.value = "";
    await router.push({ name: intent.value === "help" ? "help" : "today" });
  } catch (error) {
    failure.value =
      error instanceof ApiProblem && error.code !== null && !copy(`error_messages.${error.code}`).startsWith("error_messages.")
        ? copy(`error_messages.${error.code}`)
        : CHROME.signInFailed;
  } finally {
    busy.value = false;
  }
}

const linkLineText = computed(() => {
  if (link.value === null) {
    return copy("designed_states.unknown");
  }
  return copy(`ground_link_words.${link.value.status}`);
});
</script>

<template>
  <div class="home">
    <ContextBar
      v-if="paired"
      :read="read"
      :help-to="null"
      @help="askHelp"
    />
    <main class="home-grid">
      <section
        v-if="!paired"
        class="card paper pair"
        data-testid="pair-card"
      >
        <h1
          :id="STATION_HEADING_ID"
          class="card-title"
          tabindex="-1"
        >
          {{ copy("context_words.pair_title") }}
        </h1>
        <p class="sub">
          {{ copy("context_words.pair_hint") }}
        </p>
        <form
          class="pin-row"
          @submit.prevent="pair"
        >
          <input
            v-model="pairing"
            class="line-in"
            type="password"
            autocomplete="off"
            :placeholder="copy('context_words.station_credential')"
            :aria-label="copy('context_words.station_credential')"
          >
          <button
            class="pill do"
            type="submit"
            :disabled="pairing === ''"
          >
            {{ CHROME.signIn }}
          </button>
        </form>
        <p
          v-if="pairFailed"
          class="fine"
          role="alert"
        >
          {{ CHROME.signInFailed }}
        </p>
      </section>

      <template v-else>
        <button
          type="button"
          class="card help-entry"
          :class="{ armed: intent === 'help' }"
          data-testid="home-help"
          @click="askHelp"
        >
          <span class="help-word">{{ CHROME.someoneNeedsHelp }}</span>
          <span
            v-if="intent === 'help'"
            class="help-sub"
          >{{ copy("context_words.help_hint") }}</span>
        </button>

        <section
          id="roster"
          class="card paper roster"
          data-testid="roster"
        >
          <div class="card-head">
            <h1
              :id="STATION_HEADING_ID"
              class="card-title"
              tabindex="-1"
            >
              {{ intent === "help" ? copy("context_words.help_hint") : copy("context_words.choose_crew") }}
            </h1>
          </div>
          <p
            v-if="rosterFailed"
            class="sub"
          >
            {{ copy("context_words.roster_unavailable") }}
          </p>
          <ul class="crew-list">
            <li
              v-for="member in crew ?? []"
              :key="member.person_id"
              class="crew-row"
              :class="{ chosen: chosen === member.person_id }"
            >
              <button
                type="button"
                class="crew-pick"
                :data-person="member.person_id"
                :aria-expanded="chosen === member.person_id"
                @click="choose(member)"
              >
                <span class="avatar">{{ initials(member.display_name) }}</span>
                <span class="crew-text">
                  <b>{{ member.display_name }}</b>
                  <small>{{ roleWord(member) }} · {{ statusWord(member) }}</small>
                </span>
              </button>
              <form
                v-if="chosen === member.person_id"
                class="pin-row"
                data-testid="sign-in"
                @submit.prevent="submit"
              >
                <input
                  ref="pinEl"
                  v-model="credential"
                  class="line-in"
                  name="credential"
                  type="password"
                  autocomplete="current-password"
                  :placeholder="copy('context_words.pin')"
                  :aria-label="copy('context_words.pin')"
                >
                <button
                  class="pill do"
                  type="submit"
                  :disabled="busy || credential === ''"
                >
                  {{ intent === "help" ? CHROME.someoneNeedsHelp : copy("context_words.open_today") }}
                </button>
                <p
                  v-if="failure"
                  class="fine"
                  role="alert"
                >
                  {{ failure }}
                </p>
              </form>
            </li>
          </ul>
        </section>

        <section
          class="card paper kit-glance"
          data-testid="home-kit"
        >
          <h2 class="card-title">
            {{ copy("context_words.kit_title") }}
          </h2>
          <template v-if="kit">
            <div class="big">
              {{ kit.counts.needs_test_not_aboard }}<small>{{ CHROME.ofTotal.replace("{total}", String(kit.counts.total)) }}</small>
            </div>
            <p class="sub">
              {{ CHROME.kitNote }}
            </p>
            <div
              class="cover-bar"
              aria-hidden="true"
            >
              <i
                v-for="state in KIT_STATES"
                :key="state"
                :class="STATE_MK[state]"
                :style="{ flexGrow: kit.counts[state] }"
              />
            </div>
            <div class="cover-counts">
              <div
                v-for="state in KIT_STATES"
                :key="state"
              >
                <b>{{ kit.counts[state] }}</b>
                <span><i
                  class="mk"
                  :class="STATE_MK[state]"
                />{{ copy(`kit_map_state_words.${state}`) }}</span>
              </div>
            </div>
          </template>
          <p
            v-else
            class="sub"
          >
            {{ copy("designed_states.not_available_here") }}
          </p>
        </section>

        <section
          v-if="kit"
          class="card paper plans-glance"
          data-testid="home-plans"
        >
          <h2 class="card-title">
            {{ copy("context_words.plans_title") }}
          </h2>
          <div class="big">
            {{ kit.plans_aboard }}<small>{{ CHROME.ofTotal.replace("{total}", String(kit.counts.total)) }}</small>
          </div>
          <p class="sub">
            {{ copy("context_words.plans_line", { count: kit.plans_aboard, total: kit.counts.total }) }}
          </p>
        </section>

        <section
          class="card g g-dusk habitat-glance"
          data-testid="home-habitat"
        >
          <h2 class="card-title">
            {{ copy("context_words.habitat_title") }}
          </h2>
          <p class="hab-line">
            {{ linkLineText }}
            <SourceWord
              v-if="link"
              :origin="linkSimulated(link) ? 'SIM' : 'LIVE'"
            />
          </p>
          <component
            :is="habitatCard"
            v-if="habitatCard"
          />
        </section>

        <section
          class="card g g-deep assistant-glance"
          data-testid="home-assistant"
        >
          <h2 class="card-title">
            {{ copy("context_words.assistant_title") }}
          </h2>
          <p class="say">
            {{ assistant === "running" ? copy("context_words.assistant_running") : assistant === null ? copy("designed_states.unknown") : copy("context_words.assistant_off") }}
          </p>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.home { min-height: 100vh; display: flex; flex-direction: column; }
.home-grid { flex: 1; display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: auto auto; align-content: start; gap: 14px; padding: 18px 20px 20px; }
.card { cursor: default; }
.card:hover { transform: none; }
.pair { grid-column: 1 / span 2; align-self: start; gap: 12px; }
.help-entry { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 6px; border: 0; text-align: left; background: rgba(180, 105, 14, 0.12); color: #8a4f08; cursor: pointer; }
.help-entry:hover { background: rgba(180, 105, 14, 0.2); }
.help-entry.armed { box-shadow: inset 0 0 0 2px #8a4f08; }
.help-word { font-size: 32px; line-height: 38px; font-weight: 500; }
.help-sub { font-size: 14px; color: #8a4f08; opacity: 0.85; }
.roster { grid-column: 1; grid-row: 2; }
.crew-list { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 8px; }
.crew-row { border-radius: 16px; background: rgba(28, 30, 28, 0.035); }
.crew-row.chosen { background: rgba(226, 245, 92, 0.28); }
.crew-pick { width: 100%; display: flex; align-items: center; gap: 14px; padding: 12px 14px; border: 0; background: none; text-align: left; }
.avatar { width: 40px; height: 40px; flex: none; display: grid; place-items: center; border-radius: 50%; border: 1px solid rgba(28, 30, 28, 0.3); font-size: 13px; font-weight: 500; }
.crew-text b { display: block; font-weight: 500; font-size: 16px; }
.crew-text small { display: block; font-size: 13px; color: var(--ink-3); }
.pin-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 0 14px 14px 68px; }
.pin-row .line-in { flex: 1; min-width: 160px; }
.pin-row .fine { flex-basis: 100%; }
.pair .pin-row { padding: 0; }
.habitat-glance { grid-column: 2; grid-row: 1; gap: 8px; }
.assistant-glance { grid-column: 3; grid-row: 1; gap: 8px; }
.kit-glance { grid-column: 2; grid-row: 2; gap: 4px; align-self: start; }
.plans-glance { grid-column: 3; grid-row: 2; gap: 4px; align-self: start; }
.roster { align-self: start; }
.help-entry { min-height: 150px; }
.cover-bar { display: flex; width: 100%; height: 14px; gap: 3px; margin: 22px 0 18px; }
.cover-bar i { display: block; flex-basis: 0; min-width: 4px; border-radius: 7px; }
.cover-bar .meas { background: var(--lime); }
.cover-bar .rep { background: var(--ink); }
.cover-bar .gap { background: repeating-linear-gradient(135deg, var(--ink) 0 2px, transparent 2px 5px); box-shadow: inset 0 0 0 1px var(--ink); }
.cover-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.cover-counts b { display: block; font-size: 36px; line-height: 40px; font-weight: 300; font-variant-numeric: tabular-nums; }
.cover-counts span { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--ink-2); }
.kit-glance .fine { margin-top: 22px; font-size: 14px; color: var(--ink-2); }
.hab-line { font-size: 20px; line-height: 26px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
