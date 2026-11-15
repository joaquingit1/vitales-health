import { createApp } from "vue";
import { createWebHistory } from "vue-router";
import "./design/tokens.css";
import "./design/app.css";
import { SESSION_LOCK, SessionLock, bindBrowser, stationScreen } from "./features/identity/sessionLock";
import { CREW_API, CrewApi } from "./shell/api/client";
import { STATION, StationLink } from "./features/identity/station";
import App from "./shell/App.vue";
import { createCrewRouter } from "./shell/router";
import { SESSION, SessionStore } from "./shell/session";

function commandKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

const session = new SessionStore();
const api = new CrewApi(
  session,
  { baseUrl: window.location.origin, fetch: (request) => window.fetch(request), commandKey },
  {
    beforeRequest: () => {
      lock.check();
    },
    unauthenticated: () => {
      void lock.lock("unauthenticated");
    },
    connectionLost: () => {
      void lock.lock("connection_loss");
    },
  },
);
const router = createCrewRouter(session, () => lock.check(), createWebHistory());
const lock = new SessionLock({
  session,
  endpoints: api,
  clock: { monotonic: () => performance.now(), wall: () => Date.now() },
  timers: {
    set: (run, delayMs) => window.setTimeout(run, delayMs),
    clear: (handle) => {
      window.clearTimeout(handle);
    },
  },
  station: stationScreen(router, document),
});
bindBrowser(lock, window, document);

function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
const provisioned = (import.meta.env["VITE_CREW_STATION_CREDENTIAL"] as string | undefined) ?? null;
const station = new StationLink(window.location.origin, (request) => window.fetch(request), provisioned, tabStorage());

createApp(App).provide(SESSION, session).provide(CREW_API, api).provide(SESSION_LOCK, lock).provide(STATION, station).use(router).mount("#app");
