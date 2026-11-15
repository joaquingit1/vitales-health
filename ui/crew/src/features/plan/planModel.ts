import type { components } from "@crew-health/contracts";
import { copy } from "../../design/copy";

export type PlanView = components["schemas"]["plan-view.schema"];
export type PlanRun = components["schemas"]["plan-run.schema"];
export type KitMapView = components["schemas"]["kit-map-view.schema"];
type Stage = PlanView["stages"][number];
type StepStage = Extract<Stage, { steps: unknown }>;
type Step = StepStage["steps"][number];
type GroundStage = Extract<Stage, { link_text: unknown }>;

/** One numbered stage on the plan card: its word, the step in a sentence and the small line under it. */
export interface ShownStage {
  readonly id: string;
  readonly word: string;
  readonly text: string;
  readonly note: string;
}

/** The stages the plan card shows side by side, in plan order (the prototype's renderPlan). */
const HIDDEN = new Set(["check_another_way", "return", "ground_log"]);

function isStepStage(stage: Stage): stage is StepStage {
  return "steps" in stage;
}

function isGroundStage(stage: Stage): stage is GroundStage {
  return stage.stage_id === "ground_log";
}

function stageWord(stage: Stage): string {
  const word = copy(`plan_stage_words.${stage.stage_id}`);
  return word.startsWith("plan_stage_words.") ? stage.label : word;
}

function substituteText(stage: StepStage, step: Step): string {
  if (step.availability === "substituted" && step.substitute_step_id !== null) {
    const substitute = stage.steps.find((x) => x.step_id === step.substitute_step_id);
    if (substitute !== undefined) {
      return substitute.text;
    }
  }
  return step.text;
}

function noteFor(stage: StepStage, step: Step): string {
  if (stage.stage_id === "if_worse") {
    return stage.steps.map((x) => x.text.replace(/\s*\([^()]*\)\s*$/, "")).join("; ");
  }
  if (step.availability_text !== null) {
    return step.availability_text;
  }
  if (step.availability === "not_aboard") {
    const missing = [...step.missing.instruments, ...step.missing.supplies];
    return `${copy("plan_lines.not_aboard")}: ${missing.join(", ")}`;
  }
  if (step.recheck !== null) {
    return step.recheck.text;
  }
  const primary = stage.steps.filter((x) => !x.substitute_only);
  return primary.length > 1 ? `and ${String(primary.length - 1)} more` : "";
}

export function shownStages(plan: PlanView): ShownStage[] {
  return plan.stages.filter(isStepStage).flatMap((stage) => {
    const step = stage.steps.find((x) => !x.substitute_only) ?? stage.steps[0];
    if (HIDDEN.has(stage.stage_id) || step === undefined) {
      return [];
    }
    const text = stage.stage_id === "if_worse" ? copy("plan_lines.opens_help") : substituteText(stage, step);
    return [{ id: stage.stage_id, word: stageWord(stage), text, note: noteFor(stage, step) }];
  });
}

/** The ground stage's line, always last: the hub words it from the live link state (EI2). */
export function groundLine(plan: PlanView): string | null {
  const ground = plan.stages.find(isGroundStage);
  if (ground === undefined) {
    return null;
  }
  return ground.link_text;
}

/** "[P] of [N] have a plan aboard", counted by the hub's kit map; never a number of our own. */
export function planCountLine(map: KitMapView): string {
  const missing = map.plans_to_write.length;
  return copy(missing > 0 ? "plan_lines.plans_to_write" : "plan_lines.plans_aboard", {
    count: map.plans_aboard,
    total: map.counts.total,
    missing,
  });
}

/** The time a run started, as HH:MM in the viewer's clock. */
export function startedAt(run: PlanRun): string {
  const at = new Date(run.opened_at);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}
