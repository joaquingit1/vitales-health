import type { components } from "@crew-health/contracts";
import { copy } from "../../design/copy";

export type GroundLink = components["schemas"]["ground-link.schema"];

/** CONTRACTS section 26: whole hours rounded down when at least one hour, else whole minutes rounded down. */
export function inTime(nowMs: number, nextContactAt: string): string | null {
  const next = Date.parse(nextContactAt);
  if (!Number.isFinite(next) || !Number.isFinite(nowMs)) {
    return null;
  }
  const minutes = Math.max(0, Math.floor((next - nowMs) / 60_000));
  return minutes >= 60
    ? copy("duration_words.hours", { n: Math.floor(minutes / 60) })
    : copy("duration_words.minutes", { n: minutes });
}

/** The link's origin: only an adapter measures it; the schedule and the demo switch are simulated. */
export function linkOrigin(link: GroundLink): "LIVE" | "SIM" {
  return link.source === "measured" ? "LIVE" : "SIM";
}

/** The Habitat line: "Ground link down · next contact in 26 hours" (the origin word renders beside it). */
export function linkLine(link: GroundLink, nowMs: number): string {
  const status = copy(`ground_link_words.${link.status}`);
  if (link.status === "down" && link.next_contact_at != null) {
    const words = inTime(nowMs, link.next_contact_at);
    if (words !== null) {
      return `${status} · ${copy("designed_states.ground_next_contact_in", { in_time: words })}`;
    }
  }
  return status;
}
