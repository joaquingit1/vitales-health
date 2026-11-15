import type { Component } from "vue";

/**
 * A card on Today's "To do and cards" region. A feature adds cards by creating `features/<name>/today.ts`
 * that exports `todayCards: TodayCard[]`; the Today screen collects every such file, so no shell file is edited.
 */
export interface TodayCard {
  /** Stable id, unique across features (used as the Vue key). */
  readonly id: string;
  /** Sort position in the region, ascending. Pulse 10, teach 20, plan 30, help 40 by convention. */
  readonly order: number;
  /** The card body. It renders inside a `.card` frame from the design system; it fetches its own data. */
  readonly component: Component;
}

const slots = import.meta.glob<TodayCard[]>("../features/*/today.ts", { eager: true, import: "todayCards" });

/** Every registered Today card, sorted by `order` then `id`. */
export function todayCards(): TodayCard[] {
  return Object.values(slots)
    .flat()
    .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}
