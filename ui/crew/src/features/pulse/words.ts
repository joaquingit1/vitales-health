/**
 * Screen chrome from the interaction prototype (docs/product/design/interaction-prototype.html) that
 * config/ui-copy.json has no key for yet. Its schema (config-ui-copy.schema.json) is closed, so these wait for a
 * contract change to move there; every clinical word, value, reason, step and verdict on these screens comes from
 * the hub or from ui-copy.json. Nothing here is a value.
 */
export const WORDS = {
  pulseTitle: "Pulse at rest",
  pulseReason: "Pulse at rest. It shows how fast your heart beats now. It can't show the rhythm.",
  pulseDecision: "Whether the racing heart is fast now",
  ledeIdle: "Sit still and face the station for 30 seconds.",
  ledeRequested: "Requested. Sit still and face the station for 30 seconds.",
  ledeReading: "Reading. Keep still and face the cameras.",
  ledeFailed: "The quality check rejected this attempt.",
  keptFailed: "Kept as failed, not guessed",
  start: "Start",
  tryAgain: "Try again",
  cancel: "Cancel",
  why: "Why",
  taken: "Taken",
  quality: "Quality",
  todayTitle: "Pulse today",
  noReadingKept: "No reading, kept",
  gapTitle: "Can't test here: check it another way",
  notAboard: "Not aboard.",
  cliff: "NASA Clinical Findings Form",
  noPlanRunning: "No onboard plan is running for this yet.",
  teach: "Teach",
  notNow: "Not now",
  taps: "The taps",
  tapsNote: "One tick per tap. The picture shows the spacing; the helper's answer says what was felt.",
  aboutCheck: "About this check",
  doesAndNot: "What this does and does not tell us",
  stepOf: "Step {i} of {n}.",
  nextStep: "Next step",
  tapsLabel: "Taps",
  leftLabel: "Left",
  countAgain: "Count again",
  compared: "The count is compared with the cameras when it ends.",
  agreement: "Agreement",
  helperTitle: "Who can check",
  cardTake: "Take it at the camera station",
} as const;

export function fill(text: string, vars: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}
