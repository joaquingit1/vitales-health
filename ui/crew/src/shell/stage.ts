import { inject, reactive, type InjectionKey } from "vue";
import type { FigureMark } from "../design/body";

/**
 * What the left stage shows: the line above the headline, the headline, the marks on the figure and the region
 * in focus. The stage stays mounted across the stage routes (Today, the kit view and the feature screens), so the
 * figure builds once; a screen sets what it needs on mount.
 */
export interface StageState {
  subline: string;
  headline: string;
  marks: FigureMark[];
  focus: string | null;
  /** Bumped by a screen that has just recorded a reading, so the panel reads the latest readings again. */
  recorded: number;
}

export const STAGE: InjectionKey<StageState> = Symbol("stage");

export function createStage(): StageState {
  return reactive({ subline: "", headline: "", marks: [], focus: null, recorded: 0 });
}

export function useStage(): StageState {
  const stage = inject(STAGE);
  if (stage === undefined) {
    throw new Error("the stage was not provided");
  }
  return stage;
}
