/* The prototype's point-cloud figure (docs/product/design/body-mesh.js and body-render.js), loaded as classic
   side-effect scripts: body-mesh sets window.BODY_MESH, body-render reads it and sets window.BodyRender. */
import "./body-mesh.js";
import "./body-render.js";

export type Region =
  | "head" | "eyes" | "ear" | "neck" | "shoulder" | "heart" | "lungs" | "abdomen" | "flank" | "back"
  | "wrist" | "hand" | "legs" | "knee" | "ankle" | "band";

export interface Projected {
  readonly x: number;
  readonly y: number;
  readonly facing: number;
  readonly scale: number;
}

export interface BodyHandle {
  resize(): void;
  focus(name: string | null, options?: { camera?: boolean; ax?: number }): void;
  start(): void;
  stop(): void;
  renderNow(jump?: boolean): void;
}

export interface BodyRenderApi {
  mount(
    canvas: HTMLCanvasElement,
    options: {
      breathsPerMin?: number;
      still?: boolean;
      onFrame?: (moved: boolean) => void;
      frame?: () => { top: number; bottom: number };
    },
  ): BodyHandle;
  project(point: number[]): Projected;
  /** null when the figure has no point for the name (an unmapped region). */
  point(name: string): number[] | null;
  regionAt(x: number, y: number, names?: string[]): string | null;
}

export function bodyRender(): BodyRenderApi | null {
  const api = (window as unknown as { BodyRender?: BodyRenderApi }).BodyRender;
  return api ?? null;
}

export interface FigureMark {
  readonly region: string;
  /** dot: measured here (lime); hollow: only what you report; open: a test not aboard (dashed ring). */
  readonly kind: "dot" | "hollow" | "open";
}
