import type { components } from "@crew-health/contracts";
import type { CrewApi } from "../../../shell/api/client";

type MeasurementRequest = components["schemas"]["measurement-request.schema"];
export type Observation = components["schemas"]["observation.schema"];

/** sessionStorage key for this tab's pulse measurement requests (ids only, newest last). */
export const PULSE_REQUESTS_KEY = "crew-health.pulse.requests";
/** The card's line shows at most this many readings of this session. */
export const PULSE_REQUESTS_MAX = 7;

function readIds(): string[] {
  try {
    const raw = window.sessionStorage.getItem(PULSE_REQUESTS_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

/** The pulse screen notes each request it starts, so Today can show what it recorded. */
export function rememberPulseRequest(id: string): void {
  try {
    const ids = [...readIds().filter((known) => known !== id), id].slice(-PULSE_REQUESTS_MAX);
    window.sessionStorage.setItem(PULSE_REQUESTS_KEY, JSON.stringify(ids));
  } catch {
    /* storage refused: Today shows the request card, as with no reading */
  }
}

/** The recorded pulse observations of this tab's requests, oldest first; failed or empty attempts add nothing. */
export async function pulseReadings(api: CrewApi): Promise<Observation[]> {
  const found: Observation[] = [];
  for (const id of readIds()) {
    const request = await api.get<MeasurementRequest>(`/v1/measurement-requests/${id}`).catch(() => null);
    const success = request?.attempts.filter((a) => a.outcome === "success") ?? [];
    for (const attempt of success) {
      for (const observationId of attempt.observation_ids) {
        const observation = await api.get<Observation>(`/v1/observations/${observationId}`).catch(() => null);
        if (observation !== null && observation.code === "pulse") {
          found.push(observation);
        }
      }
    }
  }
  return found.sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
}
