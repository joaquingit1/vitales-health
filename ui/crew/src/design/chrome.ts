/**
 * TEMPORARY, pending a decision on config: only the labels no config/ui-copy.json key carries (its schema is
 * closed, so new keys would fail the config release). Text as the prototype writes it. Everything else on
 * these screens comes from ui-copy.json through copy(), or from the hub.
 */
export const CHROME = {
  brand: "Crew Health Check",
  someoneNeedsHelp: "Someone needs help",
  todo: "To do",
  habitat: "Habitat",
  thisKit: "This kit",
  kitHeadline: "What this kit can test",
  kitNote: "need a test this ship doesn't carry",
  ofTotal: "of {total}",
  exampleRows: "Example rows",
  all: "All {total}",
  form: "Form",
  kitAboard: "The kit aboard",
  signIn: "Sign in",
  person: "Crew member",
  credential: "PIN or passphrase",
  signInFailed: "Sign in failed",
} as const;

export function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}
