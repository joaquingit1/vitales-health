import type { components } from "@crew-health/contracts";
import flows from "../../../../../config/responder-flows.json";
import presentation from "../../../../../config/help-presentation.json";
import { copy } from "../../design/copy";

export type HelpSession = components["schemas"]["help-session.schema"];
export type AssistantStatus = components["schemas"]["assistant-status.schema"];

/** One screen of Someone needs help: a triage question with its answers, or a flow with its first steps. */
export interface HelpScreen {
  readonly nodeId: string;
  readonly kind: "triage" | "flow";
  readonly question: string;
  readonly hint: string;
  readonly answers: readonly { readonly index: number; readonly label: string }[];
  readonly steps: readonly { readonly index: number; readonly text: string; readonly done: boolean }[];
  readonly callGroundWhen: string;
}

/** A triage question reads as the question, then its hint (the prototype's HELP.question). */
export function splitQuestion(text: string): [string, string] {
  const at = text.indexOf("? ");
  return at > 0 ? [text.slice(0, at + 1), text.slice(at + 2)] : [text, ""];
}

/**
 * The first screen from the configuration bundled with the app (E26: on screen within 500 ms, before the hub
 * answers and with the model stopped). Answers given here are replayed to the hub as offline_path.
 */
export function cachedFirstScreen(): HelpScreen {
  const start = presentation.start_node_id;
  const node = flows.triage.find((x) => x.id === start);
  if (node === undefined) {
    throw new Error(`help-presentation start node ${start} is not in responder-flows`);
  }
  const [question, hint] = splitQuestion(node.question);
  return {
    nodeId: node.id,
    kind: "triage",
    question,
    hint,
    answers: node.answers.map((a, index) => ({ index, label: a.answer })),
    steps: [],
    callGroundWhen: "",
  };
}

/** The hub's current node as a screen. */
export function sessionScreen(session: HelpSession): HelpScreen {
  const node = session.node;
  if (node.kind === "flow" && node.flow !== null) {
    return {
      nodeId: node.id,
      kind: "flow",
      question: node.flow.title,
      hint: node.flow.plain,
      answers: [],
      steps: node.flow.immediate_steps.map((s) => ({ index: s.index, text: s.text, done: s.ticked_at !== null })),
      callGroundWhen: node.flow.call_ground_when,
    };
  }
  const [question, hint] = splitQuestion(node.question ?? "");
  return { nodeId: node.id, kind: "triage", question, hint, answers: node.answers, steps: [], callGroundWhen: "" };
}

/** Where the first screen sits in the three-part progress bar: triage, who and checks, then the flow. */
export function phase(screen: HelpScreen): number {
  if (screen.kind === "flow") {
    return 2;
  }
  return screen.nodeId === presentation.start_node_id ? 0 : 1;
}

export interface AssistantWords {
  /** "Assistant: stopped" while the model is stopped or unavailable; null while it runs. */
  readonly model: string | null;
  /** "Works without the assistant", always shown: help never waits for the model. */
  readonly works: string;
  /** The assistant card's line. */
  readonly line: string;
}

/**
 * The assistant swatches and line, worded by help-presentation assistant_line. stopped_text reads
 * "Assistant: stopped. Works without the assistant: ...": its first sentence is the model swatch and the
 * clause before the colon of the second is the swatch that is always on.
 */
export function assistantWords(state: string | null): AssistantWords {
  const stopped = presentation.assistant_line.stopped_text;
  const cut = stopped.indexOf(". ");
  const first = cut > 0 ? stopped.slice(0, cut) : stopped;
  const rest = cut > 0 ? stopped.slice(cut + 2) : "";
  const colon = rest.indexOf(":");
  const works = colon > 0 ? rest.slice(0, colon) : rest;
  if (state === "unavailable") {
    /* not stopped by anyone: the model is missing or failed, worded by ui-copy error_messages */
    const off = copy("error_messages.MODEL_UNAVAILABLE");
    const dot = off.indexOf(". ");
    return { model: dot > 0 ? off.slice(0, dot) : off, works, line: off };
  }
  const isStopped = state === "stopped" || state === "off";
  return {
    model: isStopped ? first : null,
    works,
    line: isStopped ? stopped : state === null ? "" : presentation.assistant_line.waiting_text,
  };
}

/** The model state to show: the open session's own view wins over the service status. */
export function modelState(session: HelpSession | null, status: AssistantStatus | null): string | null {
  if (session !== null) {
    return session.assistant.status;
  }
  return status === null ? null : status.state;
}

export const fixedButtons = presentation.fixed_buttons;
export const notice = presentation.notice;
