import doc from "../../../../config/ui-copy.json";

type Tree = { readonly [key: string]: string | Tree };

/**
 * Visible copy from config/ui-copy.json. `key` is a dotted path (`origin_words.SIM`); `{name}` placeholders are
 * filled from `vars`. A missing key returns the key itself so a gap is visible, never invented text.
 */
export function copy(key: string, vars: Record<string, string | number> = {}): string {
  let node: string | Tree | undefined = doc as unknown as Tree;
  for (const part of key.split(".")) {
    node = typeof node === "object" ? node[part] : undefined;
  }
  const text = typeof node === "string" ? node : key;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}

export type Origin = "LIVE" | "SIM" | "REPLAY";

/** The source word for a value's origin (Live, Sim, Replay). */
export function originWord(origin: string): string {
  return copy(`origin_words.${origin.toUpperCase()}`);
}
