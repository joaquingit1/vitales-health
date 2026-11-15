import { inject, type InjectionKey } from "vue";

/**
 * The shared screen's own principal (E02 3.4, examples/session/station-screen.json): a `station` session that reads
 * only what the contract serves to role station (the roster, the mission, the ground link, the kit map, the
 * assistant state). It never enters the personal SessionStore, so it can never mount a private route, and its
 * token stays in this object's memory. The station credential is provisioned on the screen: at launch through
 * VITE_CREW_STATION_CREDENTIAL, or typed once on the pairing card and kept for this tab only.
 */
export class StationLink {
  readonly #baseUrl: string;
  readonly #fetch: (request: Request) => Promise<Response>;
  readonly #storage: Storage | null;
  #credential: string | null;
  #token: string | null = null;
  #opening: Promise<string> | null = null;

  constructor(baseUrl: string, fetcher: (request: Request) => Promise<Response>, provisioned: string | null, storage: Storage | null) {
    this.#baseUrl = baseUrl;
    this.#fetch = fetcher;
    this.#storage = storage;
    let kept: string | null;
    try {
      kept = storage?.getItem(STATION_KEY) ?? null;
    } catch {
      kept = null;
    }
    this.#credential = provisioned !== null && provisioned !== "" ? provisioned : kept;
  }

  get paired(): boolean {
    return this.#credential !== null;
  }

  /** Signs the screen in as its station; a refused credential is forgotten and the pairing card returns. */
  async pair(credential: string): Promise<void> {
    this.#credential = credential;
    this.#token = null;
    await this.#open();
    try {
      this.#storage?.setItem(STATION_KEY, credential);
    } catch {
      /* the screen still works for this page; it asks again after a reload */
    }
  }

  /* one sign in at a time: concurrent reads share the pending one */
  #open(): Promise<string> {
    this.#opening ??= this.#signIn().finally(() => {
      this.#opening = null;
    });
    return this.#opening;
  }

  async #signIn(): Promise<string> {
    if (this.#credential === null) {
      throw new StationUnpaired();
    }
    const response = await this.#fetch(
      new Request(new URL("/v1/sessions", this.#baseUrl), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ kind: "station", credential: this.#credential }),
      }),
    );
    if (!response.ok) {
      this.#credential = null;
      try {
        this.#storage?.removeItem(STATION_KEY);
      } catch {
        /* nothing kept */
      }
      throw new StationUnpaired();
    }
    const body = (await response.json()) as { token?: string };
    if (typeof body.token !== "string") {
      throw new StationUnpaired();
    }
    this.#token = body.token;
    return body.token;
  }

  /** A GET as the station; one fresh sign in when the hub no longer knows the token. */
  async get<T>(path: string): Promise<T> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const token = this.#token ?? (await this.#open());
      const response = await this.#fetch(
        new Request(new URL(path, this.#baseUrl), {
          headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
        }),
      );
      if (response.status === 401) {
        this.#token = null;
        continue;
      }
      if (!response.ok) {
        throw new StationReadFailed(response.status);
      }
      return (await response.json()) as T;
    }
    throw new StationReadFailed(401);
  }
}

const STATION_KEY = "crew-health.station-credential";

export class StationUnpaired extends Error {
  override readonly name = "StationUnpaired";
}

export class StationReadFailed extends Error {
  override readonly name = "StationReadFailed";

  constructor(readonly status: number) {
    super(`status ${String(status)}`);
  }
}

export const STATION: InjectionKey<StationLink> = Symbol("station");

export function useStation(): StationLink | null {
  return inject(STATION, null);
}
