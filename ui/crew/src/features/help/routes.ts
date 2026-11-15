import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./Placeholder.vue";

/** Placeholder route owned by the help builder: replace Placeholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/help", name: "help", component: Placeholder, meta: { private: true } },
];
