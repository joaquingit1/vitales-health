/** Pure geometry for the card charts. No value is invented: an empty series draws nothing. */

export type Range = readonly [number, number];

/** Where `value` sits in `range`, clamped to [0, 1]. A degenerate range puts every value in the middle. */
export function fraction(value: number, range: Range): number {
  const [low, high] = range;
  if (!Number.isFinite(value) || !(high > low)) {
    return 0.5;
  }
  const at = (value - low) / (high - low);
  return Number.isFinite(at) ? Math.min(1, Math.max(0, at)) : 0.5;
}

/** The range a series spans, widened to include `extra` values (limit marks) when given. */
export function extent(values: readonly number[], extra: readonly number[] = []): Range | null {
  const all = [...values, ...extra].filter((v) => Number.isFinite(v));
  if (all.length === 0) {
    return null;
  }
  return [Math.min(...all), Math.max(...all)];
}

export interface Point {
  x: number;
  y: number;
}

/** Points of a polyline over a width by height box, x evenly spaced, y up for larger values. */
export function polyline(values: readonly number[], width: number, height: number, range: Range, pad = 4): Point[] {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length === 0) {
    return [];
  }
  const span = Math.max(finite.length - 1, 1);
  return finite.map((v, i) => ({
    x: finite.length === 1 ? width / 2 : pad + (i / span) * (width - 2 * pad),
    y: pad + (1 - fraction(v, range)) * (height - 2 * pad),
  }));
}

/** An SVG path through the points, smoothed with a cardinal curve so a short series reads like the reference. */
export function smoothPath(points: readonly Point[]): string {
  const first = points[0];
  if (first === undefined) {
    return "";
  }
  if (points.length === 1) {
    return `M${first.x},${first.y}`;
  }
  let d = `M${first.x.toFixed(2)},${first.y.toFixed(2)}`;
  points.forEach((p1, i) => {
    const p2 = points[i + 1];
    if (p2 === undefined) {
      return;
    }
    const p0 = points[i - 1] ?? p1;
    const p3 = points[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`;
  });
  return d;
}
