import type { CrewApi } from "../../shell/api/client";

interface Profile {
  readonly environment_id: string;
  readonly place?: string;
  readonly label?: string;
}

/** The place word ("Moon base") for an environment, from the served environment-profiles config document. */
export async function placeOf(api: CrewApi, environmentId: string): Promise<string | null> {
  const body = await api.get<{ document?: { profiles?: Profile[] } }>("/v1/config/environment-profiles");
  const profile = body.document?.profiles?.find((item) => item.environment_id === environmentId);
  return profile?.place ?? profile?.label ?? null;
}
