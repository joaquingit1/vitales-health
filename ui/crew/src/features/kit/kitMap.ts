import type { components } from "@crew-health/contracts";
import type { FigureMark } from "../../design/body";
import type { CrewApi } from "../../shell/api/client";

export type KitMapView = components["schemas"]["kit-map-view.schema"];
export type KitState = "measured_here" | "report_only" | "needs_test_not_aboard";
export const KIT_STATES: readonly KitState[] = ["measured_here", "report_only", "needs_test_not_aboard"];
export const STATE_MK: Record<KitState, string> = { measured_here: "meas", report_only: "rep", needs_test_not_aboard: "gap" };

/** Instrument id to its configured name: the served kit-map config's instruments, then the view's kit and
 *  candidates (the same names the kit view's other rows use). Never shows a slug when a name exists. */
export async function instrumentNames(api: CrewApi, view: KitMapView): Promise<Map<string, string>> {
  const names = new Map<string, string>();
  try {
    const body = await api.get<{ document?: { instruments?: { id: string; name?: string }[] } }>("/v1/config/kit-map");
    for (const item of body.document?.instruments ?? []) {
      if (item.name !== undefined) {
        names.set(item.id, item.name);
      }
    }
  } catch {
    /* the view's own names below still apply */
  }
  for (const item of [...view.kit, ...view.candidates]) {
    names.set(item.instrument_id, item.name);
  }
  return names;
}

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
