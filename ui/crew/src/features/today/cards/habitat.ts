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

/** Accepted series points of a sensor with a reading; a sensor with none keeps an empty line. */
export async function seriesOf(api: CrewApi, status: SensorStatus): Promise<number[]> {
  if (status.reading_state === "no_reading" || status.channel_id === null) {
    return [];
  }
  const series = await api.get<SensorSeries>(status.series_href);
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
