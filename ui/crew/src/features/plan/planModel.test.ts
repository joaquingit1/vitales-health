import { describe, expect, it } from "vitest";
import lunar from "../../../../../contracts/api/examples/plans/view-icl10-lunar.json";
import station from "../../../../../contracts/api/examples/kit-map/view-station.json";
import { groundLine, planCountLine, shownStages, type KitMapView, type PlanView } from "./planModel";

const plan = lunar.value as unknown as PlanView;
const map = station.value as unknown as KitMapView;

describe("the plan aboard", () => {
  it("shows Now, Watch, If it gets worse and Medical officer in plan order", () => {
    expect(shownStages(plan).map((s) => s.word)).toEqual(["Now", "Watch", "If it gets worse", "Medical officer"]);
  });

  it("words the worse stage as opening help, with its signs under it", () => {
    const worse = shownStages(plan).find((s) => s.id === "if_worse");
    expect(worse?.text).toBe("Someone needs help opens.");
    expect(worse?.note).toContain("Chest pain or discomfort with the racing heart");
    expect(worse?.note).not.toContain("threshold to be set");
  });

  it("shows the recheck line the hub gives for the watch stage", () => {
    expect(shownStages(plan).find((s) => s.id === "watch")?.note).toBe("Next check: interval to be set by clinical review");
  });

  it("takes the ground line from the hub's ground stage", () => {
    expect(groundLine(plan)).toBe(
      "Ground link down · next contact in 25 hours (Sim). The log goes then. Nothing here waits for it.",
    );
  });

  it("counts plans from the kit map, never from the client", () => {
    expect(planCountLine(map)).toBe("7 of 7 have a plan aboard");
    expect(planCountLine({ ...map, plans_aboard: 117, counts: { ...map.counts, total: 119 }, plans_to_write: ["ICL1", "ICL2"] })).toBe(
      "117 of 119 have a plan aboard · 2 to write",
    );
  });
});
