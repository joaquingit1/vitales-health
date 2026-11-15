import fc from "fast-check";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick, type App as VueApp } from "vue";
import { createMemoryHistory } from "vue-router";
import {
  ApiProblem,
  CREW_API,
  ConnectionLost,
  CrewApi,
  NotSignedIn,
  StaleEpoch,
  type SessionCreate,
} from "../../shell/api/client";
import App from "../../shell/App.vue";
import { createCrewRouter } from "../../shell/router";
import { ClearFailed, SESSION, SessionStore, type Session } from "../../shell/session";
import {
  STATION_HEADING_ID,
  STATION_ROUTE,
  SessionLock,
  SessionRefused,
  bindBrowser,
  sessionLife,
  stationScreen,
  type LockReason,
  type LockTimers,
  type StationScreen,
} from "./sessionLock";

const CREATED = Date.parse("2026-11-14T08:00:00Z");
const MINUTE = 60_000;
const NAMES = { mara: "Mara Okafor", ilya: "Ilya Brandt" } as const;
type Person = keyof typeof NAMES;

function crew(person: Person, token: string, life: number = 5 * MINUTE): Session {
  return {
    session_id: `ses_${person}`,
    token,
    actor: { kind: "person", id: person, display_name: NAMES[person] },
    person: { person_id: person, display_name: NAMES[person] },
    roles: ["crew"],
    mission_id: "sim_mission",
    created_at: new Date(CREATED).toISOString(),
    expires_at: new Date(CREATED + life).toISOString(),
  };
}

function signInBody(person: Person): SessionCreate {
  return { kind: "crew", person_id: person, credential: `sim passphrase for ${person}` };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function problem(status: number, code: string): Response {
  return json(status, {
    code,
    message: code,
    retryable: false,
    field_errors: [],
    correlation_id: "cor_1",
    current_state: null,
    supported_alternative: null,
  });
}

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function settle(): Promise<void> {
  for (let round = 0; round < 8; round += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

class Time {
  monotonic = 0;
  wall = CREATED;
}

class Timers implements LockTimers {
  readonly #due = new Map<number, { at: number; run: () => void }>();
  #next = 1;

  constructor(readonly time: Time) {}

  readonly set = (run: () => void, delayMs: number): number => {
    const handle = this.#next;
    this.#next += 1;
    this.#due.set(handle, { at: this.time.monotonic + delayMs, run });
    return handle;
  };

  readonly clear = (handle: number): void => {
    this.#due.delete(handle);
  };

  get pending(): number {
    return this.#due.size;
  }

  advance(ms: number, wall: number = ms): void {
    this.time.monotonic += ms;
    this.time.wall += wall;
    for (let round = 0; round < 16; round += 1) {
      const ready = [...this.#due.entries()].filter(([, timer]) => timer.at <= this.time.monotonic);
      if (ready.length === 0) {
        return;
      }
      for (const [handle, timer] of ready) {
        this.#due.delete(handle);
        timer.run();
      }
    }
  }
}

type Route = (request: Request) => Promise<Response>;

class FakeHub {
  readonly seen: { method: string; path: string; request: Request; tokenHeld: string | null }[] = [];
  readonly routes = new Map<string, Route>();
  session: SessionStore | null = null;

  readonly fetch = (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname;
    this.seen.push({ method: request.method, path, request, tokenHeld: this.session?.token ?? null });
    const route = this.routes.get(`${request.method} ${path}`);
    if (route === undefined) {
      return Promise.resolve(problem(404, "NOT_FOUND"));
    }
    return route(request);
  };

  on(method: string, path: string, route: Route): void {
    this.routes.set(`${method} ${path}`, route);
  }

  calls(method: string, path: string): Request[] {
    return this.seen.filter((call) => call.method === method && call.path === path).map((call) => call.request);
  }
}

interface Rig {
  readonly time: Time;
  readonly timers: Timers;
  readonly session: SessionStore;
  readonly hub: FakeHub;
  readonly api: CrewApi;
  readonly lock: SessionLock;
  readonly log: string[];
}

function rig(station?: StationScreen): Rig {
  const time = new Time();
  const timers = new Timers(time);
  const session = new SessionStore();
  const hub = new FakeHub();
  hub.session = session;
  const log: string[] = [];
  let keys = 0;
  const api = new CrewApi(
    session,
    {
      baseUrl: "http://hub.test",
      fetch: hub.fetch,
      commandKey: () => {
        keys += 1;
        return `signout-key-${String(keys)}`;
      },
    },
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
  const lock: SessionLock = new SessionLock({
    session,
    endpoints: api,
    clock: { monotonic: () => time.monotonic, wall: () => time.wall },
    timers,
    station: station ?? {
      show: () => {
        log.push("station");
        return Promise.resolve();
      },
    },
  });
  hub.on("DELETE", "/v1/sessions/current", () => {
    log.push("hub sign-out");
    return Promise.resolve(new Response(null, { status: 204 }));
  });
  return { time, timers, session, hub, api, lock, log };
}

async function signedIn(r: Rig, person: Person, token: string, life?: number): Promise<void> {
  r.hub.on("POST", "/v1/sessions", () => Promise.resolve(json(201, crew(person, token, life))));
  await r.lock.signIn(signInBody(person));
}

const mounted: VueApp[] = [];

afterEach(() => {
  for (const app of mounted.splice(0)) {
    app.unmount();
  }
  document.body.replaceChildren();
});

describe("T-E02-011 session deadline", () => {
  it("T-E02-011 the deadline is the session's own life counted from when sign-in was sent", async () => {
    const r = rig();
    const answer = deferred<Response>();
    r.hub.on("POST", "/v1/sessions", () => answer.promise);
    const signing = r.lock.signIn(signInBody("mara"));
    r.timers.advance(30_000);
    answer.resolve(json(201, crew("mara", "tok_a")));
    const opened = await signing;
    expect(opened.person?.person_id).toBe("mara");
    expect(r.lock.deadline).toEqual({ monotonic: 5 * MINUTE, wall: CREATED + 5 * MINUTE });
    r.timers.advance(5 * MINUTE - 30_001);
    expect(r.lock.check()).toBe(true);
    expect(r.session.current).not.toBeNull();
    r.timers.advance(1);
    expect(r.session.current).toBeNull();
    expect(r.session.token).toBeNull();
    await settle();
    expect(r.log).toEqual(["station", "hub sign-out"]);
  });

  it("T-E02-011 a clock correction can end a session early but never extend it", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.timers.advance(0, -60 * MINUTE);
    r.timers.advance(5 * MINUTE - 1, 0);
    expect(r.session.current).not.toBeNull();
    r.timers.advance(1, 0);
    expect(r.session.current).toBeNull();

    await signedIn(r, "mara", "tok_b");
    expect(r.lock.check()).toBe(true);
    r.time.wall += 5 * MINUTE;
    expect(r.lock.check()).toBe(false);
    expect(r.session.current).toBeNull();
  });

  it("T-E02-011 a paused timer is never proof of presence: the next request or navigation locks", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.time.monotonic += 5 * MINUTE;
    r.time.wall += 5 * MINUTE;
    await expect(r.api.getCurrentSession()).rejects.toBeInstanceOf(NotSignedIn);
    expect(r.hub.calls("GET", "/v1/sessions/current")).toHaveLength(0);
    expect(r.session.current).toBeNull();

    await signedIn(r, "ilya", "tok_b");
    const router = createCrewRouter(r.session, () => r.lock.check(), createMemoryHistory());
    await router.push("/profile");
    expect(router.currentRoute.value.name).toBe("profile");
    r.time.monotonic += 5 * MINUTE;
    await router.push("/");
    await router.push("/profile");
    expect(router.currentRoute.value.name).toBe(STATION_ROUTE);
    expect(r.session.current).toBeNull();
  });

  it("T-E02-011 an answer that cannot open private content never does, and its token is ended", async () => {
    const cases: { name: string; session: Session; delay: number }[] = [
      { name: "no token", session: { ...crew("mara", "unused"), token: "" }, delay: 0 },
      { name: "expiry before creation", session: { ...crew("mara", "tok_a"), expires_at: "2026-11-14T07:59:59Z" }, delay: 0 },
      { name: "unparsable instants", session: { ...crew("mara", "tok_b"), created_at: "yesterday" }, delay: 0 },
      { name: "already over on arrival", session: crew("mara", "tok_c", MINUTE), delay: MINUTE },
    ];
    for (const { name, session, delay } of cases) {
      const r = rig();
      const answer = deferred<Response>();
      r.hub.on("POST", "/v1/sessions", () => answer.promise);
      const signing = r.lock.signIn(signInBody("mara"));
      r.timers.advance(delay);
      answer.resolve(json(201, session));
      await expect(signing, name).rejects.toBeInstanceOf(SessionRefused);
      expect(r.session.current, name).toBeNull();
      expect(r.session.token, name).toBeNull();
      await settle();
      const ended = r.hub.calls("DELETE", "/v1/sessions/current").map((call) => call.headers.get("Authorization"));
      expect(ended, name).toEqual(session.token ? [`Bearer ${session.token}`] : []);
    }
  });

  it("T-E02-011 sessionLife reads only usable instants", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 4_000_000_000_000 }),
        fc.integer({ min: -86_400_000, max: 86_400_000 }),
        (created, life) => {
          const result = sessionLife({
            created_at: new Date(created).toISOString(),
            expires_at: new Date(created + life).toISOString(),
          });
          expect(result).toBe(life > 0 ? life : null);
        },
      ),
    );
    fc.assert(
      fc.property(fc.string(), fc.string(), (created, expires) => {
        const result = sessionLife({ created_at: created, expires_at: expires });
        const valid = Number.isFinite(Date.parse(created)) && Number.isFinite(Date.parse(expires));
        expect(result === null || (valid && result > 0)).toBe(true);
      }),
    );
  });
});

describe("T-E02-011 locking", () => {
  const signals: LockReason[] = [
    "deadline",
    "sign_out",
    "account_change",
    "connection_loss",
    "pagehide",
    "hidden",
    "suspend",
    "resume",
  ];

  it("T-E02-011 every lock wipes first, then shows the station screen, then ends the session on the hub", async () => {
    for (const reason of signals) {
      const r = rig();
      await signedIn(r, "mara", "tok_a");
      const before = r.session.epoch;
      const oldSignal = r.session.signal;
      r.session.onClear(() => r.log.push("clear"));
      const outcome = r.lock.lock(reason);
      expect(r.session.token, reason).toBeNull();
      expect(r.session.current, reason).toBeNull();
      expect(r.session.epoch, reason).toBe(before + 1);
      expect(oldSignal.aborted, reason).toBe(true);
      expect(await outcome, reason).toEqual({ reason, epoch: before + 1, server: "signed_out" });
      expect(r.log, reason).toEqual(["clear", "station", "hub sign-out"]);
      const [ended] = r.hub.seen.filter((call) => call.method === "DELETE");
      expect(ended?.tokenHeld, reason).toBeNull();
      expect(ended?.request.headers.get("Authorization"), reason).toBe("Bearer tok_a");
      expect(ended?.request.keepalive, reason).toBe(true);
      expect(ended?.request.headers.get("Idempotency-Key")?.length, reason).toBeGreaterThanOrEqual(8);
      expect(r.timers.pending, reason).toBe(0);
    }
  });

  it("T-E02-011 server sign-out is best effort after the local wipe", async () => {
    for (const failing of [
      () => Promise.resolve(problem(500, "INTERNAL_ERROR")),
      () => Promise.reject(new TypeError("network down")),
    ]) {
      const r = rig();
      await signedIn(r, "mara", "tok_a");
      r.hub.on("DELETE", "/v1/sessions/current", failing);
      const outcome = await r.lock.signOut();
      expect(outcome.server).toBe("not_signed_out");
      expect(r.session.current).toBeNull();
      expect(r.session.token).toBeNull();
    }
  });

  it("T-E02-011 a lock with nothing open still fences the epoch and shows the station screen", async () => {
    const r = rig();
    const outcome = await r.lock.lock("pagehide");
    expect(outcome).toEqual({ reason: "pagehide", epoch: 1, server: "not_attempted" });
    expect(r.log).toEqual(["station"]);
  });

  it("T-E02-011 a failing clear handler still completes the wipe and then fails loudly", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    const cleared: string[] = [];
    r.session.onClear(() => {
      throw new Error("store refused to clear");
    });
    r.session.onClear(() => cleared.push("drafts"));
    await expect(r.lock.signOut()).rejects.toBeInstanceOf(ClearFailed);
    expect(cleared).toEqual(["drafts"]);
    expect(r.session.token).toBeNull();
    expect(r.log).toEqual(["station", "hub sign-out"]);
  });

  it("T-E02-011 an unregistered clear handler no longer runs", async () => {
    const r = rig();
    const cleared: string[] = [];
    const stop = r.session.onClear(() => cleared.push("journal draft"));
    await r.lock.lock("hidden");
    stop();
    await r.lock.lock("hidden");
    expect(cleared).toEqual(["journal draft"]);
  });
});

describe("T-E02-011 epochs and the hub's answers", () => {
  it("T-E02-011 late responses and their 401s from an old epoch never reach a store or lock the next person", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    const late = deferred<Response>();
    r.hub.on("GET", "/v1/sessions/current", () => late.promise);
    const store: { session: Session | null } = { session: null };
    const reading = r.api.getCurrentSession().then((session) => {
      store.session = session;
    });
    await settle();
    await r.lock.signOut();
    await signedIn(r, "ilya", "tok_b");
    late.resolve(problem(401, "UNAUTHENTICATED"));
    await expect(reading).rejects.toBeInstanceOf(StaleEpoch);
    expect(store.session).toBeNull();
    expect(r.session.current?.person?.person_id).toBe("ilya");

    const lateSuccess = deferred<Response>();
    r.hub.on("GET", "/v1/sessions/current", () => lateSuccess.promise);
    const second = r.api.getCurrentSession();
    await settle();
    await r.lock.lock("hidden");
    lateSuccess.resolve(json(200, crew("ilya", "tok_b")));
    await expect(second).rejects.toBeInstanceOf(StaleEpoch);
  });

  it("T-E02-011 locking aborts what is in flight", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.hub.on(
      "GET",
      "/v1/sessions/current",
      (request) =>
        new Promise((_, reject) => {
          request.signal.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const reading = r.api.getCurrentSession();
    await settle();
    await r.lock.lock("suspend");
    await expect(reading).rejects.toBeInstanceOf(StaleEpoch);
  });

  it("T-E02-011 a 401 locks without asking the hub to end the session again", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.hub.on("GET", "/v1/sessions/current", () => Promise.resolve(problem(401, "UNAUTHENTICATED")));
    const failure = await r.api.getCurrentSession().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiProblem);
    expect(failure instanceof ApiProblem ? [failure.status, failure.code] : null).toEqual([401, "UNAUTHENTICATED"]);
    expect(r.session.current).toBeNull();
    await settle();
    expect(r.hub.calls("DELETE", "/v1/sessions/current")).toHaveLength(0);
    expect(r.log).toEqual(["station"]);
  });

  it("T-E02-011 a lost connection locks", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.hub.on("GET", "/v1/sessions/current", () => Promise.reject(new TypeError("network down")));
    await expect(r.api.getCurrentSession()).rejects.toBeInstanceOf(ConnectionLost);
    expect(r.session.current).toBeNull();
  });

  it("T-E02-011 the token travels only in the Authorization header", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_secret");
    r.hub.on("GET", "/v1/sessions/current", () => Promise.resolve(json(200, crew("mara", "tok_secret"))));
    await r.lock.verify();
    const [sent] = r.hub.calls("GET", "/v1/sessions/current");
    expect(sent?.headers.get("Authorization")).toBe("Bearer tok_secret");
    expect(sent?.url).not.toContain("tok_secret");
    expect(r.hub.calls("POST", "/v1/sessions")[0]?.url).toBe("http://hub.test/v1/sessions");
  });

  it("T-E02-011 an account change wipes the first person before the second is asked for", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    const store: { owner: Person | null } = { owner: "mara" };
    r.session.onClear(() => {
      store.owner = null;
    });
    const answer = deferred<Response>();
    r.hub.on("POST", "/v1/sessions", () => {
      r.log.push(`sign-in asked; session ${String(r.session.current)}; store ${String(store.owner)}`);
      return answer.promise;
    });
    const signing = r.lock.signIn(signInBody("ilya"));
    await settle();
    expect(r.log).toEqual(["station", "hub sign-out", "sign-in asked; session null; store null"]);
    answer.resolve(json(201, crew("ilya", "tok_b")));
    expect((await signing).person?.person_id).toBe("ilya");
    expect(r.hub.calls("DELETE", "/v1/sessions/current")[0]?.headers.get("Authorization")).toBe("Bearer tok_a");
  });

  it("T-E02-011 the hub naming another session locks as an account change", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    r.hub.on("GET", "/v1/sessions/current", () => Promise.resolve(json(200, crew("ilya", "tok_a"))));
    const lockSpy = vi.spyOn(r.lock, "lock");
    expect(await r.lock.verify()).toBeNull();
    expect(lockSpy).toHaveBeenCalledWith("account_change");
    expect(r.session.current).toBeNull();
    expect(await r.lock.verify()).toBeNull();
  });

  it("T-E02-011 a session that arrives after its epoch ended is ended on the hub and dropped", async () => {
    const r = rig();
    const answer = deferred<Response>();
    r.hub.on("POST", "/v1/sessions", () => answer.promise);
    const signing = r.lock.signIn(signInBody("mara"));
    await settle();
    await r.lock.lock("hidden");
    answer.resolve(json(201, crew("mara", "tok_late")));
    await expect(signing).rejects.toBeInstanceOf(StaleEpoch);
    expect(r.session.current).toBeNull();
    await settle();
    const ended = r.hub.calls("DELETE", "/v1/sessions/current").map((call) => call.headers.get("Authorization"));
    expect(ended).toEqual(["Bearer tok_late"]);
  });

  it("T-E02-011 a refused sign-in names the contract code and opens nothing", async () => {
    const r = rig();
    r.hub.on("POST", "/v1/sessions", () => Promise.resolve(problem(422, "IDENTITY_MISMATCH")));
    const failure = await r.lock.signIn(signInBody("mara")).catch((error: unknown) => error);
    expect(failure instanceof ApiProblem ? failure.code : null).toBe("IDENTITY_MISMATCH");
    expect(r.session.current).toBeNull();
    expect(r.session.epoch).toBe(0);
  });
});

describe("T-E02-011 browser binding", () => {
  function hidden(state: DocumentVisibilityState): void {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  }

  it("T-E02-011 leaving, hiding, suspending, resuming, a back-forward restore and going offline all lock", async () => {
    const cases: { fire: () => void; reason: LockReason | null }[] = [
      { fire: () => window.dispatchEvent(new Event("pagehide")), reason: "pagehide" },
      { fire: () => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })), reason: "resume" },
      { fire: () => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false })), reason: null },
      {
        fire: () => {
          hidden("hidden");
          document.dispatchEvent(new Event("visibilitychange"));
        },
        reason: "hidden",
      },
      {
        fire: () => {
          hidden("visible");
          document.dispatchEvent(new Event("visibilitychange"));
        },
        reason: null,
      },
      { fire: () => document.dispatchEvent(new Event("freeze")), reason: "suspend" },
      { fire: () => document.dispatchEvent(new Event("resume")), reason: "resume" },
      { fire: () => window.dispatchEvent(new Event("offline")), reason: "connection_loss" },
    ];
    for (const { fire, reason } of cases) {
      const r = rig();
      await signedIn(r, "mara", "tok_a");
      const lockSpy = vi.spyOn(r.lock, "lock");
      const unbind = bindBrowser(r.lock, window, document);
      fire();
      unbind();
      hidden("visible");
      expect(lockSpy.mock.calls.map(([called]) => called), String(reason)).toEqual(reason === null ? [] : [reason]);
      expect(r.session.current === null, String(reason)).toBe(reason !== null);
      await settle();
    }
  });

  it("T-E02-011 unbinding stops every listener", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    bindBrowser(r.lock, window, document)();
    window.dispatchEvent(new Event("pagehide"));
    window.dispatchEvent(new Event("offline"));
    expect(r.session.current).not.toBeNull();
  });

  it("T-E02-011 scripted input, polling and responses never extend the session", async () => {
    const r = rig();
    await signedIn(r, "mara", "tok_a");
    const deadline = r.lock.deadline;
    const checks = vi.spyOn(r.lock, "check");
    const unbind = bindBrowser(r.lock, window, document);
    r.hub.on("GET", "/v1/sessions/current", () => Promise.resolve(json(200, crew("mara", "tok_a"))));
    for (let minute = 1; minute < 5; minute += 1) {
      r.timers.advance(MINUTE - 1);
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
      window.dispatchEvent(new Event("pointerdown"));
      window.dispatchEvent(new Event("touchstart"));
      await r.lock.verify();
      r.timers.advance(1);
    }
    expect(checks.mock.calls.length).toBe(4);
    expect(r.lock.deadline).toEqual(deadline);
    r.timers.advance(MINUTE - 1);
    expect(r.session.current).not.toBeNull();
    r.timers.advance(1);
    expect(r.session.current).toBeNull();
    unbind();
  });
});

describe("T-E02-011 the shell", () => {
  async function shell(): Promise<{ r: Rig; router: ReturnType<typeof createCrewRouter>; host: HTMLElement }> {
    const host = document.createElement("div");
    document.body.append(host);
    let router: ReturnType<typeof createCrewRouter> | null = null;
    const r = rig({
      show: () => {
        if (router === null) {
          return Promise.reject(new Error("router missing"));
        }
        return stationScreen(router, document).show();
      },
    });
    router = createCrewRouter(r.session, () => r.lock.check(), createMemoryHistory());
    const app = createApp(App).provide(SESSION, r.session).provide(CREW_API, r.api).use(router);
    mounted.push(app);
    app.mount(host);
    await router.push("/");
    await settle();
    return { r, router, host };
  }

  it("T-E02-011 a private route never mounts without a session, and Back cannot bring one back", async () => {
    const { r, router, host } = await shell();
    await router.push("/profile");
    expect(router.currentRoute.value.name).toBe(STATION_ROUTE);
    expect(host.querySelector(".profile")).toBeNull();

    await signedIn(r, "mara", "tok_a");
    await router.push("/somewhere");
    expect(router.currentRoute.value.name).toBe(STATION_ROUTE);
    await router.push("/profile");
    await router.push({ path: "/profile", query: { view: "again" } });
    await settle();
    expect(host.textContent).toContain("Mara Okafor");

    const locking = r.lock.lock("hidden");
    await nextTick();
    expect(host.textContent).not.toContain("Mara Okafor");
    await locking;
    await settle();
    expect(router.currentRoute.value.name).toBe(STATION_ROUTE);
    expect(document.activeElement?.id).toBe(STATION_HEADING_ID);

    router.back();
    await settle();
    expect(router.currentRoute.value.name).toBe(STATION_ROUTE);
    expect(host.querySelector(".profile")).toBeNull();
    expect(host.textContent).not.toContain("Mara Okafor");
  });

  it("T-E02-011 a new epoch remounts the view so no component draft survives", async () => {
    const { r, router, host } = await shell();
    await signedIn(r, "mara", "tok_a");
    await router.push("/profile");
    await settle();
    const first = host.querySelector(".profile");
    expect(first).not.toBeNull();
    await r.lock.lock("hidden");
    await signedIn(r, "mara", "tok_b");
    await router.push("/profile");
    await settle();
    const second = host.querySelector(".profile");
    expect(second).not.toBeNull();
    expect(second).not.toBe(first);
  });

  it("T-E02-011 no token is persisted, shown or put in an address", async () => {
    const { r, router, host } = await shell();
    await signedIn(r, "mara", "tok_never_stored");
    await router.push("/profile");
    await settle();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(window.location.href).not.toContain("tok_never_stored");
    expect(router.currentRoute.value.fullPath).not.toContain("tok_never_stored");
    expect(host.innerHTML).not.toContain("tok_never_stored");
    expect(JSON.stringify(r.session.state.value)).not.toContain("tok_never_stored");
  });
});
