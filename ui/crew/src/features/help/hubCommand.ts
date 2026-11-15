import { ApiProblem, StaleEpoch } from "../../shell/api/client";
import type { SessionStore } from "../../shell/session";

function commandKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * A POST that carries If-Match, which CrewApi.post does not take yet. Fenced the same way: sent with the
 * epoch's token and abort signal, and a result from an ended epoch is discarded as StaleEpoch.
 */
export async function postIfMatch<T>(session: SessionStore, path: string, body: unknown, revision: number): Promise<T> {
  const token = session.token;
  if (token === null) {
    throw new ApiProblem(401, "UNAUTHENTICATED");
  }
  const epoch = session.epoch;
  const response = await fetch(new URL(path, window.location.origin), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": commandKey(),
      "If-Match": `"${String(revision)}"`,
    },
    body: JSON.stringify(body),
    signal: session.signal,
  });
  if (session.epoch !== epoch) {
    throw new StaleEpoch("the response belonged to an ended session epoch");
  }
  const parsed: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const code =
      typeof parsed === "object" && parsed !== null && "code" in parsed && typeof parsed.code === "string" ? parsed.code : null;
    throw new ApiProblem(response.status, code);
  }
  return parsed as T;
}
