import { copy } from "./copy";

/**
 * A hub refusal in the designed states' words (UX-DESIGN 1.7, 1.8): ui-copy error_messages when it has the code,
 * else "Not available here". The raw code never becomes the line; it stays on the failure for logs and tests.
 */
export function refusalText(code: string | null | undefined): string {
  if (typeof code === "string" && code !== "") {
    const key = `error_messages.${code}`;
    const text = copy(key);
    if (text !== key) {
      return text;
    }
  }
  return copy("designed_states.not_available_here");
}
