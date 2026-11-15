import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./Placeholder.vue";

/** Placeholder route owned by the teach builder: replace Placeholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/teach/:id", name: "teach", component: Placeholder, meta: { private: true } },
];
