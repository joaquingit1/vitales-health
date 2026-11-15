import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./HelpPlaceholder.vue";

/** Placeholder route owned by the help builder: replace HelpPlaceholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/help", name: "help", component: Placeholder, meta: { private: true } },
];
