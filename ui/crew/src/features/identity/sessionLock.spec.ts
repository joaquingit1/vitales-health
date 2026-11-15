import { expect, test, type Page } from "@playwright/test";
import type { CrewApi } from "../../shell/api/client";
import type { SessionStore, SessionView } from "../../shell/session";
import { STATION_HEADING_ID, type Deadline, type SessionLock } from "./sessionLock";

/**
 * The lock in a real browser against a real hub: hub/tests/privacy/e02_station.py serves a seeded SIM
 * station behind the Vite dev server (playwright.config.ts). The app's own sign-in controls stay off until
 * the X-E03-1 words exist, so each test composes the production modules in the page, as main.ts does, with
 * a recording station screen and a skewable clock, and signs in through the hub's createSession.
 */

/** The SIM test secret hub/tests/privacy/e02_station.py sets for each seeded principal. */
function credential(principal: string): string {
  return `sim passphrase for ${principal}`;
}

interface Lab {
  readonly session: SessionStore;
  readonly api: CrewApi;
  readonly lock: SessionLock;
  readonly reasons: string[];
  readonly store: { owner: string | null };
  readonly skew: { ms: number };
  pending: Promise<string> | null;
}

declare global {
  var e02Lab: Lab | undefined;
}

const MODULES = {
  session: "/src/shell/session.ts",
  client: "/src/shell/api/client.ts",
  lock: "/src/features/identity/sessionLock.ts",
};

async function lab(page: Page, person: string): Promise<SessionView> {
  await page.goto("/");
  return page.evaluate(
    async ({ modules, person, secret }) => {
      const sessionModule: typeof import("../../shell/session") = await import(modules.session);
      const clientModule: typeof import("../../shell/api/client") = await import(modules.client);
      const lockModule: typeof import("./sessionLock") = await import(modules.lock);
      const session = new sessionModule.SessionStore();
      const skew = { ms: 0 };
      const reasons: string[] = [];
      let lock: SessionLock | null = null;
      const api = new clientModule.CrewApi(
        session,
        { baseUrl: location.origin, fetch: (request) => fetch(request), commandKey: () => crypto.randomUUID() },
        {
          beforeRequest: () => {
            lock?.check();
          },
          unauthenticated: () => {
            void lock?.lock("unauthenticated");
          },
          connectionLost: () => {
            void lock?.lock("connection_loss");
          },
        },
      );
      const created = new lockModule.SessionLock({
        session,
        endpoints: api,
        clock: { monotonic: () => performance.now() + skew.ms, wall: () => Date.now() + skew.ms },
        timers: {
          set: (run, delayMs) => window.setTimeout(run, delayMs),
          clear: (handle) => {
            window.clearTimeout(handle);
          },
        },
        station: { show: () => Promise.resolve() },
      });
      const original = created.lock.bind(created);
      created.lock = (reason) => {
        reasons.push(reason);
        return original(reason);
      };
      lock = created;
      const store: { owner: string | null } = { owner: null };
      session.onClear(() => {
        store.owner = null;
      });
      lockModule.bindBrowser(created, window, document);
      globalThis.e02Lab = { session, api, lock: created, reasons, store, skew, pending: null };
      const opened = await created.signIn({ kind: "crew", person_id: person, credential: secret });
      store.owner = opened.person?.person_id ?? null;
      return opened;
    },
    { modules: MODULES, person, secret: credential(person) },
  );
}

function state(page: Page) {
  return page.evaluate(() => {
    const current = globalThis.e02Lab;
    if (current === undefined) {
      return null;
    }
    return {
      person: current.session.current?.person?.person_id ?? null,
      token: current.session.token !== null,
      owner: current.store.owner,
      reasons: [...current.reasons],
    };
  });
}

async function token(page: Page): Promise<string> {
  const value = await page.evaluate(() => globalThis.e02Lab?.session.token ?? null);
  if (value === null) {
    throw new Error("no session is open in the page");
  }
  return value;
}

test("T-E02-011 a private address opened directly shows only the station screen, and history cannot restore it", async ({
  page,
}) => {
  await page.goto("/profile");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(`#${STATION_HEADING_ID}`)).toBeAttached();
  await expect(page.locator(".profile")).toHaveCount(0);
  await page.goBack();
  await page.goForward();
  await expect(page.locator(".profile")).toHaveCount(0);
});

test("T-E02-011 a real session opens with the hub's life as its deadline and leaves no token anywhere", async ({
  page,
  context,
}) => {
  const before = Date.now();
  const opened = await lab(page, "mara");
  expect(opened.person?.person_id).toBe("mara");
  const life = Date.parse(opened.expires_at) - Date.parse(opened.created_at);
  const deadline: Deadline | null = await page.evaluate(() => globalThis.e02Lab?.lock.deadline ?? null);
  expect(deadline).not.toBeNull();
  expect(deadline?.wall ?? 0).toBeGreaterThanOrEqual(before + life);
  expect(deadline?.wall ?? Infinity).toBeLessThanOrEqual(Date.now() + life);
  const secret = await token(page);
  const stored = await page.evaluate(() => ({
    local: localStorage.length,
    session: sessionStorage.length,
    cookie: document.cookie,
    html: document.documentElement.outerHTML,
    href: location.href,
  }));
  expect(stored.local).toBe(0);
  expect(stored.session).toBe(0);
  expect(stored.cookie).toBe("");
  expect(stored.html).not.toContain(secret);
  expect(stored.href).not.toContain(secret);
  expect(await context.cookies()).toEqual([]);
});

test("T-E02-011 a session ended on the hub locks at the next request with a 401", async ({ page }) => {
  await lab(page, "ilya");
  const secret = await token(page);
  const ended = await page.request.delete("/v1/sessions/current", {
    headers: { Authorization: `Bearer ${secret}`, "Idempotency-Key": "spec-ends-ilya-on-the-hub" },
  });
  expect(ended.status()).toBe(204);
  const failure = await page.evaluate(() =>
    globalThis.e02Lab?.lock.verify().then(
      () => "applied",
      (error: unknown) => (error instanceof Error ? `${error.name} ${error.message}` : "unknown"),
    ),
  );
  expect(failure).toBe("ApiProblem UNAUTHENTICATED");
  expect(await state(page)).toEqual({ person: null, token: false, owner: null, reasons: ["unauthenticated"] });
});

test("T-E02-011 a request in flight when the lock comes is cut off and its answer never applied", async ({ page }) => {
  await lab(page, "sofia");
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/v1/sessions/current", async (route) => {
    if (route.request().method() === "GET") {
      await held;
    }
    await route.continue().catch(() => undefined);
  });
  await page.evaluate(() => {
    const current = globalThis.e02Lab;
    if (current !== undefined) {
      current.pending = current.lock.verify().then(
        () => "applied",
        (error: unknown) => (error instanceof Error ? error.name : "unknown"),
      );
    }
  });
  await page.evaluate(() => globalThis.e02Lab?.lock.signOut().then(() => undefined));
  release();
  expect(await page.evaluate(() => globalThis.e02Lab?.pending)).toBe("StaleEpoch");
  expect(await state(page)).toEqual({ person: null, token: false, owner: null, reasons: ["sign_out"] });
});

test("T-E02-011 going offline locks", async ({ page, context }) => {
  await lab(page, "tomas");
  await context.setOffline(true);
  await expect.poll(() => state(page)).toEqual({
    person: null,
    token: false,
    owner: null,
    reasons: ["connection_loss"],
  });
  await context.setOffline(false);
});

test("T-E02-011 a trusted key re-checks the deadline; scripted input and polling never extend it", async ({
  page,
}) => {
  await lab(page, "mara");
  const deadline = await page.evaluate(() => globalThis.e02Lab?.lock.deadline ?? null);
  for (let poll = 0; poll < 3; poll += 1) {
    await page.evaluate(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
      window.dispatchEvent(new PointerEvent("pointerdown"));
      await globalThis.e02Lab?.lock.verify();
    });
  }
  expect(await page.evaluate(() => globalThis.e02Lab?.lock.deadline ?? null)).toEqual(deadline);
  await page.evaluate(() => {
    const current = globalThis.e02Lab;
    const end = current?.lock.deadline;
    if (current !== undefined && end) {
      current.skew.ms = end.monotonic - performance.now() + 1;
    }
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
  });
  expect((await state(page))?.person).toBe("mara");
  await page.keyboard.press("Shift");
  await expect.poll(() => state(page)).toEqual({ person: null, token: false, owner: null, reasons: ["deadline"] });
});

test("T-E02-011 leaving the page locks it and ends the session on the hub; coming back shows no private content", async ({
  page,
}) => {
  await lab(page, "sofia");
  const secret = await token(page);
  await page.goto("/v1/health");
  await expect
    .poll(async () => (await page.request.get("/v1/sessions/current", { headers: { Authorization: `Bearer ${secret}` } })).status())
    .toBe(401);
  await page.goBack();
  await expect(page.locator(`#${STATION_HEADING_ID}`)).toBeAttached();
  await expect(page.locator(".profile")).toHaveCount(0);
  const restored = await state(page);
  expect(restored === null || (restored.person === null && !restored.token && restored.owner === null)).toBe(true);
});
