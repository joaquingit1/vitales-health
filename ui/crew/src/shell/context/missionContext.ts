import type { components } from "@crew-health/contracts";

export type GroundLink = components["schemas"]["ground-link.schema"];
export type Health = components["schemas"]["health.schema"];
type Mission = components["schemas"]["mission-context.schema"];

/** Any authenticated GET: the signed-in client's `get`, or the sign-in screen's short station read. */
export type Read = <T>(path: string) => Promise<T>;

interface Profile {
  readonly environment_id: string;
  readonly label?: string;
  readonly place?: string;
}

/**
 * What the context bar shows, every value from the hub. `missionDay` and `met` are null when the hub does not
 * serve the mission clock (getMission is in the contract; a hub that has not composed it answers 404), and the
 * bar then says so instead of guessing a day.
 */
export interface MissionContext {
  readonly environmentId: string | null;
  readonly environmentLabel: string | null;
  readonly place: string | null;
  readonly missionMode: string | null;
  readonly missionDay: number | null;
  readonly met: string | null;
  readonly link: GroundLink | null;
  readonly serverTime: number;
}

async function optional<T>(pending: Promise<T>): Promise<T | null> {
  try {
    return await pending;
  } catch {
    return null;
  }
}

/** Reads the mission clock (getMission), the ground link (getGroundLink), the hub time (getHealth) and the
 *  served environment profiles; each part that fails stays null and the bar shows it as unknown. */
export async function readMissionContext(read: Read): Promise<MissionContext> {
  const [mission, link, health, profiles] = await Promise.all([
    optional(read<Mission>("/v1/mission")),
    optional(read<GroundLink>("/v1/ground-link")),
    optional(read<Health>("/v1/health")),
    optional(read<{ document?: { profiles?: Profile[] } }>("/v1/config/environment-profiles")),
  ]);
  const environmentId = mission?.active_profile.environment_id ?? link?.environment_id ?? null;
  const profile = profiles?.document?.profiles?.find((item) => item.environment_id === environmentId);
  return {
    environmentId,
    environmentLabel: mission?.active_profile.label ?? profile?.label ?? null,
    place: mission?.active_profile.place ?? profile?.place ?? null,
    missionMode: mission?.mission_mode ?? null,
    missionDay: mission?.today.mission_day ?? null,
    met: mission?.met ?? null,
    link: mission?.ground_link ?? link,
    serverTime: Date.parse(health?.server_time ?? ""),
  };
}

/** The environment's icon by its kind of place; an environment this list does not know gets the generic mark. */
export function environmentIcon(environmentId: string | null): "station" | "transit" | "moon" | "mars" | "generic" {
  switch (environmentId) {
    case "station_leo":
      return "station";
    case "transit":
      return "transit";
    case "lunar_surface":
      return "moon";
    case "mars_surface":
      return "mars";
    default:
      return "generic";
  }
}

/** A link is measured only by a comms adapter; the schedule and the demo switch are simulated (E29). */
export function linkSimulated(link: GroundLink | null): boolean {
  return link !== null && link.source !== "measured";
}
