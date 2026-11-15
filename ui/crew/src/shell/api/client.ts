import { client as createClient } from "@crew-health/contracts";
import type { components } from "@crew-health/contracts";
import type { Session, SessionStore } from "../session";

export type SessionCreate = components["schemas"]["SessionCreate"];
export type Health = components["schemas"]["health.schema"];

/** The result belongs to an epoch that has ended; it never reaches a store or the screen. */
export class StaleEpoch extends Error {
  override readonly name = "StaleEpoch";
}

/** An authenticated operation was asked for while no session is open; nothing was sent. */
export class NotSignedIn extends Error {
  override readonly name = "NotSignedIn";
}

/** The hub could not be reached. */
export class ConnectionLost extends Error {
  override readonly name = "ConnectionLost";
}

/** The hub answered with an error status; `code` is the contract error code when the body carried one. */
export class ApiProblem extends Error {
  override readonly name = "ApiProblem";

  constructor(
    readonly status: number,
    readonly code: string | null,
  ) {
    super(code ?? `status ${String(status)}`);
  }
}

export interface ApiHooks {
  /** Runs before an authenticated request is sent; the session lock re-checks its deadline here. */
  readonly beforeRequest: () => void;
  readonly unauthenticated: () => void;
  readonly connectionLost: () => void;
}

export interface ApiOptions {
  readonly baseUrl: string;
  readonly fetch: (request: Request) => Promise<Response>;
  readonly commandKey: () => string;
}

interface Outcome<T> {
  readonly data?: T;
  readonly error?: unknown;
  readonly response: Response;
}

function errorCode(body: unknown): string | null {
  if (typeof body === "object" && body !== null && "code" in body && typeof body.code === "string") {
    return body.code;
  }
  return null;
}

function aborted(failure: unknown): boolean {
  return typeof failure === "object" && failure !== null && "name" in failure && failure.name === "AbortError";
}

/**
 * The generated contract client behind the session epoch (E03 7.3). Every call captures the epoch it was sent
 * under and the epoch's abort signal; whatever comes back after that epoch ended is discarded as StaleEpoch
 * before any caller sees it, including a 401 that belongs to an old token.
 */
export class CrewApi {
  readonly #http: ReturnType<typeof createClient>;
  readonly #session: SessionStore;
  readonly #options: ApiOptions;
  readonly #hooks: ApiHooks;

  constructor(session: SessionStore, options: ApiOptions, hooks: ApiHooks) {
    this.#http = createClient({ baseUrl: options.baseUrl, fetch: options.fetch });
    this.#session = session;
    this.#options = options;
    this.#hooks = hooks;
  }

  async getHealth(): Promise<Health> {
    const epoch = this.#session.epoch;
    const pending = this.#http.GET("/v1/health", { signal: this.#session.signal });
    return this.#required(await this.#settle(epoch, pending));
  }

  /** Sends no token; the session lock fences the result itself so it can sign out a session it refuses. */
  async createSession(body: SessionCreate, signal: AbortSignal): Promise<Session> {
    const pending = this.#http.POST("/v1/sessions", { body, signal });
    let outcome: Outcome<Session>;
    try {
      outcome = await pending;
    } catch (failure) {
      if (aborted(failure)) {
        throw new StaleEpoch("sign-in was aborted", { cause: failure });
      }
      this.#hooks.connectionLost();
      throw new ConnectionLost("the hub could not be reached", { cause: failure });
    }
    if (!outcome.response.ok) {
      throw new ApiProblem(outcome.response.status, errorCode(outcome.error));
    }
    return this.#required(outcome);
  }

  async getCurrentSession(): Promise<Session> {
    const { epoch, authorization } = this.#authorized();
    const pending = this.#http.GET("/v1/sessions/current", {
      headers: authorization,
      signal: this.#session.signal,
    });
    return this.#required(await this.#settle(epoch, pending));
  }

  /**
   * Ends a session on the hub with the token it was opened with. Used only after the local wipe, so it is
   * bound to no epoch, outlives page unload (`keepalive`) and calls no hook: its failure changes nothing here.
   */
  async deleteCurrentSession(token: string): Promise<void> {
    const outcome = await this.#http.DELETE("/v1/sessions/current", {
      params: { header: { "Idempotency-Key": this.#options.commandKey() } },
      headers: { Authorization: `Bearer ${token}` },
      keepalive: true,
    });
    if (!outcome.response.ok) {
      throw new ApiProblem(outcome.response.status, errorCode(outcome.error));
    }
  }

  #authorized(): { epoch: number; authorization: { Authorization: string } } {
    this.#hooks.beforeRequest();
    const token = this.#session.token;
    if (token === null) {
      throw new NotSignedIn("no session is open");
    }
    return { epoch: this.#session.epoch, authorization: { Authorization: `Bearer ${token}` } };
  }

  async #settle<T>(epoch: number, pending: Promise<Outcome<T>>): Promise<Outcome<T>> {
    let outcome: Outcome<T>;
    try {
      outcome = await pending;
    } catch (failure) {
      if (aborted(failure) || this.#session.epoch !== epoch) {
        throw new StaleEpoch("the request belonged to an ended session epoch", { cause: failure });
      }
      this.#hooks.connectionLost();
      throw new ConnectionLost("the hub could not be reached", { cause: failure });
    }
    if (this.#session.epoch !== epoch) {
      throw new StaleEpoch("the response belonged to an ended session epoch");
    }
    if (outcome.response.status === 401) {
      this.#hooks.unauthenticated();
    }
    if (!outcome.response.ok) {
      throw new ApiProblem(outcome.response.status, errorCode(outcome.error));
    }
    return outcome;
  }

  #required<T>(outcome: Outcome<T>): T {
    if (outcome.data === undefined) {
      throw new ApiProblem(outcome.response.status, null);
    }
    return outcome.data;
  }
}
