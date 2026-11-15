import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./Placeholder.vue";

/** Placeholder route owned by the plan builder: replace Placeholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/plan/:id", name: "plan", component: Placeholder, meta: { private: true } },
];
