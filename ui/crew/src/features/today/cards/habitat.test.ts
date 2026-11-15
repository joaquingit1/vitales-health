import { describe, expect, it } from "vitest";
import { SERIES_SPAN_MS, seriesPath, type SensorStatus } from "./habitat";

function status(observedAt: string | null, channel: string | null = "ch_co2_cabin_replay"): SensorStatus {
  return {
    channel_id: channel,
    series_href: "/v1/sensors/co2/series",
    latest_reading: observedAt === null ? null : { observed_at: observedAt },
  } as unknown as SensorStatus;
}

describe("seriesPath", () => {
  it("bounds the read to the span ending at the newest reading", () => {
    const path = seriesPath(status("2014-10-07T15:36:00Z"));
    const query = new URLSearchParams((path ?? "").split("?")[1]);
    expect(path?.startsWith("/v1/sensors/co2/series?")).toBe(true);
    expect(query.get("to")).toBe("2014-10-07T15:36:00.000Z");
    expect(Date.parse(query.get("to") ?? "") - Date.parse(query.get("from") ?? "")).toBe(SERIES_SPAN_MS);
  });

  it("reads nothing for a sensor with no reading or no channel", () => {
    expect(seriesPath(status(null))).toBeNull();
    expect(seriesPath(status("2014-10-07T15:36:00Z", null))).toBeNull();
  });
});
