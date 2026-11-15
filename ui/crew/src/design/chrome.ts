/**
 * App chrome labels, as the prototype (docs/product/design/interaction-prototype.html) writes them. These are
 * the labels config/ui-copy.json has no key for; its schema is closed (additionalProperties false), so adding
 * keys there would fail the config release. Every value-bearing word (origin, states, link, designed states)
 * comes from ui-copy.json through copy().
 */
export const CHROME = {
  brand: "Crew Health Check",
  someoneNeedsHelp: "Someone needs help",
  todo: "To do",
  today: "today",
  habitat: "Habitat",
  thisKit: "This kit",
  kitHeadline: "What this kit can test",
  kitNote: "need a test this ship doesn't carry",
  ofTotal: "of {total}",
  exampleRows: "Example rows",
  backToToday: "Back to Today",
  all: "All {total}",
  form: "Form",
  needs: "needs {instruments}",
  aboard: "aboard",
  noInstrument: "no instrument needed",
  kitAboard: "The kit aboard",
  kitMapUnavailable: "The kit map is not available from the hub.",
  linkUnavailable: "The ground link is not available from the hub.",
  todoUnavailable: "The routine is not available from the hub.",
  signIn: "Sign in",
  person: "Crew member",
  credential: "PIN or passphrase",
  signInFailed: "Sign in failed",
} as const;

export function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}
