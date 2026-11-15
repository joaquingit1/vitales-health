import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./PlanPlaceholder.vue";

/** Placeholder route owned by the plan builder: replace PlanPlaceholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/plan/:id", name: "plan", component: Placeholder, meta: { private: true } },
];
