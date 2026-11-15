import type { RouteRecordRaw } from "vue-router";
import MedicinesView from "./MedicinesView.vue";

export const routes: RouteRecordRaw[] = [
  { path: "/medicines", name: "medicines", component: MedicinesView, meta: { private: true } },
];
