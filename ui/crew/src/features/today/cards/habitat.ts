import type { components } from "@crew-health/contracts";
import type { CrewApi } from "../../../shell/api/client";
import type { Range } from "../../../design/charts/scale";

export type SensorStatus = components["schemas"]["sensor-status.schema"];
export type SensorSeries = components["schemas"]["sensor-series.schema"];

/** One channel tile of the Habitat card: every field is the hub's, nothing is filled in. */
export interface ChannelTile {
  id: string;
  name: string;
  text: string;
  unit: string;
  value: number | null;
  range: Range;
  limits: number[];
  limitLabels: string[];
  level: string | null;
  origin: string;
  sourceLabel: string;
  current: boolean;
  series: number[];
}

/** The reference shows four channel tiles. */
export const TILE_MAX = 4;

export function tileOf(status: SensorStatus): ChannelTile {
  const [low = 0, high = 0] = status.display_range;
  return {
    id: status.sensor_type_id,
    name: status.name,
    text: status.display_value.text,
    unit: status.display_value.value === null ? "" : status.display_value.unit,
    value: status.display_value.value ?? null,
    range: [low, high],
    limits: status.limit_marks.map((mark) => mark.value),
    limitLabels: status.limit_marks.map((mark) => mark.label),
    level: status.shown_level ?? null,
    origin: status.origin_mode,
    sourceLabel: status.source_label,
    current: status.reading_state === "current",
    series: [],
  };
}

/** The tile line covers this span before the newest reading. A drawing bound, not content: a whole replay file is
    more than sensors.series_max_points, and getSensorSeries refuses an unbounded read of it (INVALID_VALUE). */
export const SERIES_SPAN_MS = 6 * 60 * 60 * 1000;

/** getSensorSeries for the span ending at the newest reading, or null when the sensor has none to end on. */
export function seriesPath(status: SensorStatus): string | null {
  const newest = status.latest_reading?.observed_at;
  const end = newest === undefined ? Number.NaN : Date.parse(newest);
  if (status.channel_id === null || !Number.isFinite(end)) {
    return null;
  }
  const query = new URLSearchParams({
    from: new Date(end - SERIES_SPAN_MS).toISOString(),
    to: new Date(end).toISOString(),
  });
  return `${status.series_href}?${query.toString()}`;
}

/** Accepted series points of a sensor with a reading; a sensor with none keeps an empty line. */
export async function seriesOf(api: CrewApi, status: SensorStatus): Promise<number[]> {
  const path = seriesPath(status);
  if (path === null) {
    return [];
  }
  const series = await api.get<SensorSeries>(path);
  return series.points.map((point) => point.value);
}

/** The legend under the tiles: each distinct source once, in tile order. */
export function sourcesOf(tiles: readonly ChannelTile[]): { origin: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const tile of tiles) {
    if (!seen.has(tile.sourceLabel)) {
      seen.set(tile.sourceLabel, tile.origin);
    }
  }
  return [...seen.entries()].map(([label, origin]) => ({ origin, label }));
}
