/**
 * Assistant card chrome from UX-DESIGN.md (section 5, the Assistant card; section 6, step 4) that
 * config/ui-copy.json and config/assistant-ui.json have no key for yet. Both schemas are closed, so these wait for a
 * contract change to move there, like features/pulse/words.ts. The reply, its limitations and the kit check come
 * from the hub. Nothing here is a value.
 */
export const ASK_WORDS = {
  placeholder: "Ask about a reading",
  takeReading: "Take reading",
  requested: "Requested.",
  openPulse: "Take it at the camera station",
} as const;
