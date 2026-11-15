import type { RouteRecordRaw } from "vue-router";
import Placeholder from "./Placeholder.vue";

/** Placeholder route owned by the pulse builder: replace Placeholder.vue (or this file) with the real screen. */
export const routes: RouteRecordRaw[] = [
  { path: "/pulse", name: "pulse", component: Placeholder, meta: { private: true } },
];
