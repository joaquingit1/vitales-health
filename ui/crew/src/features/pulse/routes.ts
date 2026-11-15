import type { RouteRecordRaw } from "vue-router";
import PulseView from "./PulseView.vue";

/** Shot C: the pulse request, the camera attempt and the gap card (E05, E06, E27). */
export const routes: RouteRecordRaw[] = [
  { path: "/pulse", name: "pulse", component: PulseView, meta: { private: true } },
];
