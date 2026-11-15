import type { components } from "@crew-health/contracts";
import type { FigureMark } from "../../design/body";
import type { CrewApi } from "../../shell/api/client";

export type KitMapView = components["schemas"]["kit-map-view.schema"];
export type KitState = "measured_here" | "report_only" | "needs_test_not_aboard";
export const KIT_STATES: readonly KitState[] = ["measured_here", "report_only", "needs_test_not_aboard"];
export const STATE_MK: Record<KitState, string> = { measured_here: "meas", report_only: "rep", needs_test_not_aboard: "gap" };

export function loadKitMap(api: CrewApi): Promise<KitMapView> {
  return api.get<KitMapView>("/v1/kit-map");
}

/** The figure's marks from the hub's per-region marks: a dashed ring where a test is not aboard, then a lime
 *  dot where something is measured here, else a hollow ring where only a report is possible (prototype setKitSpots). */
export function figureMarks(view: KitMapView): FigureMark[] {
  const marks: FigureMark[] = [];
  for (const region of view.regions) {
    const present = new Set<string>(region.marks);
    if (present.has("needs_test_not_aboard")) {
      marks.push({ region: region.region, kind: "open" });
    }
    if (present.has("measured_here")) {
      marks.push({ region: region.region, kind: "dot" });
    } else if (present.has("report_only")) {
      marks.push({ region: region.region, kind: "hollow" });
    }
  }
  return marks;
}
