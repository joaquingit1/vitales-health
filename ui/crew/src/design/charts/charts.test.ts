import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { createApp, h } from "vue";
import SparkLine from "./SparkLine.vue";
import LimitBar from "./LimitBar.vue";
import { extent, fraction, polyline, smoothPath } from "./scale";

function render(component: unknown, props: Record<string, unknown>): HTMLElement {
  const host = document.createElement("div");
  createApp({ render: () => h(component as never, props) }).mount(host);
  return host;
}

describe("chart scale", () => {
  it("keeps every fraction inside the track", () => {
    fc.assert(
      fc.property(fc.double({ noNaN: true }), fc.double({ noNaN: true }), fc.double({ noNaN: true }), (v, a, b) => {
        const f = fraction(v, [Math.min(a, b), Math.max(a, b)]);
        expect(f).toBeGreaterThanOrEqual(0);
        expect(f).toBeLessThanOrEqual(1);
      }),
    );
  });

  it("draws nothing for an empty series and never invents a point", () => {
    expect(extent([])).toBeNull();
    expect(polyline([], 100, 40, [0, 1])).toEqual([]);
    expect(smoothPath([])).toBe("");
    expect(polyline([1, Number.NaN, 3], 100, 40, [0, 4])).toHaveLength(2);
  });

  it("puts larger values higher", () => {
    const [low, high] = polyline([1, 9], 100, 40, [0, 10]);
    expect(high?.y ?? Number.NaN).toBeLessThan(low?.y ?? Number.NaN);
  });
});

describe("SparkLine", () => {
  it("renders the line and its end dot only when there are values", () => {
    expect(render(SparkLine, { values: [], label: "CO2" }).querySelector("svg")).toBeNull();
    const host = render(SparkLine, { values: [3.1, 3.4, 3.2], label: "CO2" });
    expect(host.querySelector("path.spark-line")?.getAttribute("d")).toMatch(/^M/);
    expect(host.querySelectorAll("circle")).toHaveLength(2);
    expect(host.querySelector("svg")?.getAttribute("aria-label")).toBe("CO2");
  });
});

describe("LimitBar", () => {
  it("marks each limit and fills only a real value", () => {
    const empty = render(LimitBar, { value: null, range: [0, 5], limits: [3] });
    expect(empty.querySelector(".lbar-fill")).toBeNull();
    expect(empty.querySelectorAll('[data-testid="limit-mark"]')).toHaveLength(1);
    expect((empty.querySelector('[data-testid="limit-mark"]') as HTMLElement).style.left).toBe("60%");
    const filled = render(LimitBar, { value: 2.5, range: [0, 5], limits: [3] });
    expect((filled.querySelector(".lbar-fill") as HTMLElement).style.width).toBe("50%");
  });
});
