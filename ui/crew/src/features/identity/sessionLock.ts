/**
 * The crew app's session lock (E02 section 7, deadline rule in section 3.2).
 *
 * Private content needs an open session whose deadline is known. The wire carries no idle budget, so the
 * deadline is the session's own life (`expires_at` minus `created_at`) counted from when the sign-in request
 * was sent: a slow response cannot add network time. Until R-E02-3 defines a renewable presence contract,
 * nothing renews it. A trusted key, pointer or touch only makes the lock re-check the deadline at once, since a
 * throttled or paused timer is never proof of presence; scripted events, polling, responses and stream
 * traffic never count. The deadline is checked on both the monotonic and the wall clock and the earlier one
 * wins, so a clock correction in either direction can end a session early but never extend it.
 *
 * Locking wipes first and talks to the hub last: it ends the E03 session epoch (token forgotten, requests and
 * streams aborted, registered private state cleared), replaces the route with the station screen and moves
 * focus to its heading, and only then asks the hub to end the session, best effort. Late results of the old
 * epoch are discarded by the API client before they reach a store.
 */
import { nextTick, type InjectionKey } from "vue";
import type { Router } from "vue-router";
import { StaleEpoch, type SessionCreate } from "../../shell/api/client";
import type { Session, SessionStore, SessionView } from "../../shell/session";

export type LockReason =
  | "deadline"
  | "sign_out"
  | "unauthenticated"
  | "account_change"
  | "connection_loss"
  | "pagehide"
  | "hidden"
  | "suspend"
  | "resume";

export type ServerSignOut = "signed_out" | "not_signed_out" | "not_attempted";

export const STATION_ROUTE = "sign-in";
export const STATION_HEADING_ID = "station-heading";

const LONGEST_TIMER_MS = 2_147_483_647;

export interface LockClock {
  readonly monotonic: () => number;
  readonly wall: () => number;
}

export interface LockTimers {
  readonly set: (run: () => void, delayMs: number) => number;
  readonly clear: (handle: number) => void;
}

export interface SessionEndpoints {
  createSession(body: SessionCreate, signal: AbortSignal): Promise<Session>;
  getCurrentSession(): Promise<Session>;
  deleteCurrentSession(token: string): Promise<void>;
}

export interface StationScreen {
  show(): Promise<void>;
}

export interface LockDeps {
  readonly session: SessionStore;
  readonly endpoints: SessionEndpoints;
  readonly clock: LockClock;
  readonly timers: LockTimers;
  readonly station: StationScreen;
}

export interface Deadline {
  readonly monotonic: number;
  readonly wall: number;
}

export interface LockOutcome {
  readonly reason: LockReason;
  readonly epoch: number;
  readonly server: ServerSignOut;
}

/** The hub's answer cannot open private content: no token, no usable life, or a deadline already passed. */
export class SessionRefused extends Error {
  override readonly name = "SessionRefused";
}

/** The session's life in milliseconds, or null when its instants are unusable. */
export function sessionLife(session: Pick<Session, "created_at" | "expires_at">): number | null {
  const created = Date.parse(session.created_at);
  const expires = Date.parse(session.expires_at);
  if (!Number.isFinite(created) || !Number.isFinite(expires) || expires <= created) {
    return null;
  }
  return expires - created;
}

function samePrincipal(held: SessionView, current: Session): boolean {
  return (
    held.session_id === current.session_id &&
    held.actor.kind === current.actor.kind &&
    held.actor.id === current.actor.id &&
    (held.person?.person_id ?? null) === (current.person?.person_id ?? null)
  );
}

export class SessionLock {
  readonly #deps: LockDeps;
  #deadline: Deadline | null = null;
  #timer: number | null = null;

  constructor(deps: LockDeps) {
    this.#deps = deps;
  }

  get deadline(): Deadline | null {
    return this.#deadline;
  }

  /** Signs in; a session already open is locked first, so nothing of one person survives into the next. */
  async signIn(body: SessionCreate): Promise<SessionView> {
    const { session, endpoints, clock } = this.#deps;
    if (session.token !== null) {
      await this.lock("account_change");
    }
    const dispatched: Deadline = { monotonic: clock.monotonic(), wall: clock.wall() };
    const epoch = session.epoch;
    const created = await endpoints.createSession(body, session.signal);
    const token = created.token ?? null;
    if (session.epoch !== epoch) {
      void this.#signOutOnHub(token);
      throw new StaleEpoch("the session arrived after its epoch ended");
    }
    const life = sessionLife(created);
    const deadline =
      life === null ? null : { monotonic: dispatched.monotonic + life, wall: dispatched.wall + life };
    if (token === null || token === "" || deadline === null || this.#passed(deadline)) {
      void this.#signOutOnHub(token);
      throw new SessionRefused("the hub's answer cannot open private content");
    }
    const opened = session.begin(created, token);
    this.#deadline = deadline;
    this.#arm();
    return opened;
  }

  signOut(): Promise<LockOutcome> {
    return this.lock("sign_out");
  }

  /**
   * Wipes synchronously, then shows the station screen and asks the hub to end the old session. The promise
   * settles once both finished; it rejects only when private state could not be cleared, after the rest ran.
   */
  lock(reason: LockReason): Promise<LockOutcome> {
    const { session, station } = this.#deps;
    const token = session.token;
    this.#disarm();
    this.#deadline = null;
    let failed = false;
    let failure: unknown = null;
    try {
      session.end();
    } catch (error) {
      failed = true;
      failure = error;
    }
    const epoch = session.epoch;
    const shown = station.show();
    const server: Promise<ServerSignOut> =
      reason === "unauthenticated" ? Promise.resolve("not_attempted") : this.#signOutOnHub(token);
    return Promise.all([shown, server]).then(([, signedOut]) => {
      if (failed) {
        throw failure;
      }
      return { reason, epoch, server: signedOut };
    });
  }

  /** True while the session is open; locks and answers false once its deadline has passed. */
  check(): boolean {
    const deadline = this.#deadline;
    if (deadline === null) {
      return false;
    }
    if (this.#passed(deadline)) {
      void this.lock("deadline");
      return false;
    }
    return true;
  }

  /** Asks the hub who this session is; a different session or principal locks as an account change. */
  async verify(): Promise<SessionView | null> {
    const { session, endpoints } = this.#deps;
    const held = session.current;
    if (held === null) {
      return null;
    }
    const current = await endpoints.getCurrentSession();
    if (!samePrincipal(held, current)) {
      await this.lock("account_change");
      return null;
    }
    return held;
  }

  #passed(deadline: Deadline): boolean {
    const { clock } = this.#deps;
    return clock.monotonic() >= deadline.monotonic || clock.wall() >= deadline.wall;
  }

  #arm(): void {
    const deadline = this.#deadline;
    if (deadline === null) {
      return;
    }
    const { clock, timers } = this.#deps;
    const remaining = Math.min(deadline.monotonic - clock.monotonic(), deadline.wall - clock.wall());
    this.#timer = timers.set(
      () => {
        this.#timer = null;
        if (this.check()) {
          this.#arm();
        }
      },
      Math.min(Math.max(remaining, 0), LONGEST_TIMER_MS),
    );
  }

  #disarm(): void {
    if (this.#timer !== null) {
      this.#deps.timers.clear(this.#timer);
      this.#timer = null;
    }
  }

  #signOutOnHub(token: string | null): Promise<ServerSignOut> {
    if (token === null || token === "") {
      return Promise.resolve("not_attempted");
    }
    return this.#deps.endpoints.deleteCurrentSession(token).then(
      () => "signed_out",
      () => "not_signed_out",
    );
  }
}

export const SESSION_LOCK: InjectionKey<SessionLock> = Symbol("session lock");

/**
 * Binds the browser's lifecycle signals to the lock and returns the unbinding. Any of them locks: leaving or
 * hiding the page, suspension and resumption, a page restored from the back-forward cache, the browser going
 * offline. Only trusted human key, pointer and touch input re-checks the deadline.
 */
export function bindBrowser(lock: SessionLock, target: Window, doc: Document): () => void {
  const binding = new AbortController();
  const options = { capture: true, signal: binding.signal };
  const when = (node: EventTarget, type: string, reason: LockReason, applies: (event: Event) => boolean) => {
    node.addEventListener(
      type,
      (event) => {
        if (applies(event)) {
          void lock.lock(reason);
        }
      },
      options,
    );
  };
  const always = () => true;
  when(target, "pagehide", "pagehide", always);
  when(target, "pageshow", "resume", (event) => "persisted" in event && event.persisted === true);
  when(doc, "visibilitychange", "hidden", () => doc.visibilityState === "hidden");
  when(doc, "freeze", "suspend", always);
  when(doc, "resume", "resume", always);
  when(target, "offline", "connection_loss", always);
  for (const type of ["keydown", "pointerdown", "touchstart"]) {
    target.addEventListener(
      type,
      (event) => {
        if (event.isTrusted) {
          lock.check();
        }
      },
      { ...options, passive: true },
    );
  }
  return () => {
    binding.abort();
  };
}

/** The E03 station route as the lock's destination: replaces the current entry and focuses its heading. */
export function stationScreen(router: Router, doc: Document): StationScreen {
  return {
    async show() {
      await router.replace({ name: STATION_ROUTE });
      await nextTick();
      doc.getElementById(STATION_HEADING_ID)?.focus();
    },
  };
}
