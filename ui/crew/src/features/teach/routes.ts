import type { RouteRecordRaw } from "vue-router";
import TeachView from "./TeachView.vue";

/** Shot D: teach and check on a plan run; `id` is the plan_run_id (E30, E31). */
export const routes: RouteRecordRaw[] = [
  { path: "/teach/:id", name: "teach", component: TeachView, meta: { private: true } },
];
