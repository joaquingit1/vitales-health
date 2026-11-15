import { afterEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick, type App } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import TeachView from "./TeachView.vue";
import { CREW_API, CrewApi } from "../../shell/api/client";
import { SESSION, SessionStore, type Session } from "../../shell/session";
import { STAGE, createStage } from "../../shell/stage";
import { copy } from "../../design/copy";
import agree from "../../../../../contracts/api/examples/plans/guided-check-agree.json";
import disagree from "../../../../../contracts/api/examples/plans/guided-check-disagree.json";
import run from "../../../../../contracts/api/examples/plans/run-icl10-opened.json";
import roles from "../../../../../contracts/api/examples/roles/plan-run-roles.json";
import plan from "../../../../../contracts/api/examples/plans/view-icl10-lunar.json";
import catalog from "../../../../../config/skills-catalog.json";

/**
 * The teach screen renders what the hub sends: the contract's own examples stand in for the hub's answers
 * (T30-01 agree, T30-02 disagree, E31 officer on a walk). Values, verdicts and the helper row come from those
 * payloads and from skills-catalog.json, never from this file.
 */
const skill = catalog.skills.find((s) => s.skill_id === "pulse_check_wrist");
const names = { helper_name: "Tomas", subject_name: "Mara" };
const steps = {
  guided_check_id: agree.value.guided_check_id,
  revision: 1,
  card_title_text: (skill?.card_title ?? "").replace(/\{(\w+)\}/g, (_, k: keyof typeof names) => names[k]),
  notice: "notice",
  review_state: "unreviewed",
  teach_steps: (skill?.teach_steps ?? []).map((s) => ({
    ...s,
    text: (s.text ?? "").replace(/\{(\w+)\}/g, (_, k: keyof typeof names) => names[k]),
    landmark_label: null,
  })),
};

let app: App | null = null;
afterEach(() => {
  app?.unmount();
  app = null;
});

async function mount(id: string, answers: Record<string, unknown>): Promise<HTMLElement> {
  const fetch = vi.fn((request: Request) => {
    const path = new URL(request.url).pathname;
    const body = answers[path];
    return Promise.resolve(new Response(JSON.stringify(body ?? { code: "NOT_FOUND" }), { status: body ? 200 : 404 }));
  });
  const session = new SessionStore();
  session.begin(
    { session_id: "ses_1", actor: { kind: "person", id: "mara", display_name: "Mara Okafor" }, person: { person_id: "mara", display_name: "Mara Okafor" }, roles: ["crew"], mission_id: "m", created_at: "2026-11-15T10:00:00Z", expires_at: "2026-11-15T18:00:00Z", token: "t" } as unknown as Session,
    "t",
  );
  const api = new CrewApi(session, { baseUrl: "http://hub", fetch, commandKey: () => "k".repeat(16) }, { beforeRequest: () => undefined, unauthenticated: () => undefined, connectionLost: () => undefined });
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/teach/:id", name: "teach", component: TeachView }] });
  await router.push(`/teach/${id}`);
  const host = document.createElement("div");
  app = createApp({ template: "<RouterView />" });
  app.provide(SESSION, session).provide(CREW_API, api).provide(STAGE, createStage()).use(router).mount(host);
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await nextTick();
  }
  return host;
}

const text = (host: HTMLElement, test: string): string => host.querySelector(`[data-test="${test}"]`)?.textContent?.trim() ?? "";

describe("teach and check", () => {
  it("shows the helper row with the officer on a walk and Teach Tomas (E31)", async () => {
    const host = await mount("prn_icl10_mara_1", {
      "/v1/plan-runs/prn_icl10_mara_1": run.value,
      "/v1/plan-runs/prn_icl10_mara_1/roles": roles.value,
      "/v1/plans/plan_icl10": plan.value,
    });
    expect(text(host, "officer")).toBe("Ilya Brandt, medical officer, is on a surface walk until 11:40.");
    expect(text(host, "teach")).toBe("Teach Tomas");
  });

  it("lands the hub's agree verdict and keeps the ring line (T30-01)", async () => {
    const id = agree.value.guided_check_id;
    const host = await mount(id, { [`/v1/guided-checks/${id}`]: agree.value, [`/v1/guided-checks/${id}/steps`]: steps });
    expect(text(host, "verdict")).toContain("Your count 92 · cameras 96 · they agree");
    expect(text(host, "ring-line")).toContain("Only an ECG can.");
    expect(host.querySelectorAll('[data-test="tick"]').length).toBe(agree.value.beats.length);
    expect(text(host, "camera-reference")).toContain("96");
  });

  it("shows the designed disagree result with its correction and a retry, no value (T30-02)", async () => {
    const id = disagree.value.guided_check_id;
    const host = await mount(id, { [`/v1/guided-checks/${id}`]: disagree.value, [`/v1/guided-checks/${id}/steps`]: steps });
    expect(text(host, "verdict")).toContain(copy("guided_check_words.result_disagree"));
    expect(text(host, "verdict")).toContain(disagree.value.result_text);
  });
});
