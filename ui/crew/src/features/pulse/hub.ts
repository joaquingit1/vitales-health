import { refusalText } from "../../design/refusal";
import type { components } from "@crew-health/contracts";
import { ApiProblem, StaleEpoch } from "../../shell/api/client";
import type { SessionStore } from "../../shell/session";

type S = components["schemas"];
export type MeasurementRequest = S["measurement-request.schema"];
export type Observation = S["observation.schema"];
export type Capability = S["capability.schema"];
export type KitMapView = S["kit-map-view.schema"];
export type PlanRun = S["plan-run.schema"];
export type RoleAssignment = S["role-assignment.schema"];
export type GuidedCheck = S["guided-check.schema"];

/** GET /v1/guided-checks/{id}/steps: the pinned, rendered teach steps (teach/application/queries.py project_steps). */
export interface TeachStep {
  readonly step_no: number;
  readonly kind: "instruction" | "count" | "question";
  readonly actor: string;
  readonly text: string;
  readonly text_first_time: string | null;
  readonly landmark_id: string | null;
  readonly landmark_label: string | null;
  readonly body_region: string | null;
  readonly count_seconds: number | null;
  readonly choices: readonly { readonly answer: "regular" | "irregular" | "not_sure"; readonly label: string }[];
  readonly source_ids: readonly string[];
}
export interface TeachSteps {
  readonly guided_check_id: string;
  readonly revision: number;
  readonly card_title_text: string;
  readonly notice: string;
  readonly review_state: string;
  readonly teach_steps: readonly TeachStep[];
}

/** A config document as GET /v1/config/{name} serves it. */
export interface ConfigDocument<T> {
  readonly document: T;
}
export interface KitMapInstrument {
  readonly id: string;
  readonly name: string;
  readonly needs_text?: string;
}

function commandKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Commands that need `If-Match` (measurement request actions, guided check actions, role actions). Same fencing as
 * the shell client: the session token, the epoch's abort signal, and a result from an ended epoch is discarded.
 */
export async function command<T>(session: SessionStore, path: string, body: unknown, revision?: number): Promise<T> {
  const token = session.token;
  const epoch = session.epoch;
  if (token === null) {
    throw new ApiProblem(401, "UNAUTHENTICATED");
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": commandKey(),
  };
  if (revision !== undefined) {
    headers["If-Match"] = `"${String(revision)}"`;
  }
  const response = await fetch(new URL(path, window.location.origin), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: session.signal,
  });
  if (session.epoch !== epoch) {
    throw new StaleEpoch("the response belonged to an ended session epoch");
  }
  const text = await response.text();
  const parsed: unknown = text === "" ? undefined : JSON.parse(text);
  if (!response.ok) {
    const code =
      typeof parsed === "object" && parsed !== null && "code" in parsed && typeof parsed.code === "string"
        ? parsed.code
        : null;
    throw new ApiProblem(response.status, code);
  }
  return parsed as T;
}

/** The refused state's words: the designed state, never the raw code (design/refusal.ts). */
export function refusal(failure: unknown): string {
  if (failure instanceof ApiProblem) {
    return refusalText(failure.code);
  }
  if (failure instanceof StaleEpoch) {
    return "";
  }
  return refusalText(null);
}

export const FINISHED = new Set(["completed", "unavailable", "declined", "cancelled", "failed"]);

/** "Heart rhythm (ECG)" reads "Heart rhythm" on the gap card, as the prototype's plainName does. */
export function plainName(label: string): string {
  return label.replace(/\s*\([^()]*\)\s*$/, "");
}

/** A hub device id as words: `camera_2` reads "camera 2". */
export function deviceWords(deviceId: string | null | undefined): string {
  return (deviceId ?? "").replace(/_/g, " ");
}

export function firstName(name: string): string {
  return name.split(" ")[0] ?? name;
}

export const sleep = (ms: number): Promise<void> => new Promise((resolve) => window.setTimeout(resolve, ms));
