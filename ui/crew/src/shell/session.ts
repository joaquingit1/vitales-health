import { inject, shallowRef, type InjectionKey, type ShallowRef } from "vue";
import type { components } from "@crew-health/contracts";

export type Session = components["schemas"]["session.schema"];

/** A session as the screens may read it: everything but the token, which only the API client sends. */
export interface SessionView {
  readonly session_id: Session["session_id"];
  readonly actor: Readonly<Session["actor"]>;
  readonly person: Readonly<NonNullable<Session["person"]>> | null;
  readonly roles: readonly Session["roles"][number][];
  readonly mission_id: Session["mission_id"];
  readonly created_at: Session["created_at"];
  readonly expires_at: Session["expires_at"];
}

export interface SessionState {
  readonly epoch: number;
  readonly session: SessionView | null;
}

/** Raised by `end` after the whole wipe ran, when one or more registered clear handlers failed. */
export class ClearFailed extends AggregateError {
  override readonly name = "ClearFailed";
}

function view(session: Session): SessionView {
  return Object.freeze({
    session_id: session.session_id,
    actor: Object.freeze({ ...session.actor }),
    person: session.person === null ? null : Object.freeze({ ...session.person }),
    roles: Object.freeze([...session.roles]),
    mission_id: session.mission_id,
    created_at: session.created_at,
    expires_at: session.expires_at,
  });
}

/**
 * The session epoch and the only copy of the personal token (SYSTEM 8.3, E03 7.2). Memory only: nothing here
 * is ever written to storage, a cookie or a URL. Each epoch has its own abort signal; every request and stream
 * started under an epoch is aborted when it ends, and a result that arrives under a later epoch is discarded.
 */
export class SessionStore {
  readonly #state: ShallowRef<SessionState> = shallowRef({ epoch: 0, session: null });
  readonly #clears = new Set<() => void>();
  #token: string | null = null;
  #controller = new AbortController();

  get state(): Readonly<ShallowRef<SessionState>> {
    return this.#state;
  }

  get epoch(): number {
    return this.#state.value.epoch;
  }

  get current(): SessionView | null {
    return this.#state.value.session;
  }

  get token(): string | null {
    return this.#token;
  }

  get signal(): AbortSignal {
    return this.#controller.signal;
  }

  /** Opens a new epoch for a session that has a token; a session already open must be ended first. */
  begin(session: Session, token: string): SessionView {
    if (this.#token !== null) {
      throw new Error("a session is already open; end it before beginning another");
    }
    const opened = view(session);
    this.#advance(opened);
    this.#token = token;
    return opened;
  }

  /**
   * Ends the epoch: forgets the token, aborts everything started under it and runs every clear handler.
   * Every handler runs even when one fails; the failures are raised together afterwards.
   */
  end(): number {
    this.#token = null;
    this.#advance(null);
    const failures: unknown[] = [];
    for (const clear of [...this.#clears]) {
      try {
        clear();
      } catch (failure) {
        failures.push(failure);
      }
    }
    if (failures.length > 0) {
      throw new ClearFailed(failures, "private state could not be cleared");
    }
    return this.epoch;
  }

  /** Registers private state to clear at every epoch end; returns the unregistration. */
  onClear(clear: () => void): () => void {
    this.#clears.add(clear);
    return () => {
      this.#clears.delete(clear);
    };
  }

  #advance(session: SessionView | null): void {
    this.#controller.abort();
    this.#controller = new AbortController();
    this.#state.value = { epoch: this.#state.value.epoch + 1, session };
  }
}

export const SESSION: InjectionKey<SessionStore> = Symbol("session");

export function useSession(): SessionStore {
  const session = inject(SESSION);
  if (session === undefined) {
    throw new Error("the session store was not provided");
  }
  return session;
}
