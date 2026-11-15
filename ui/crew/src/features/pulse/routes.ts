import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./PulsePlaceholder.vue";

/** Placeholder route owned by the pulse builder: replace PulsePlaceholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/pulse", name: "pulse", component: Placeholder, meta: { private: true } },
];
