import type { RouteRecordRaw } from "vue-router";
import KitView from "./KitView.vue";

export const routes: RouteRecordRaw[] = [{ path: "/kit", name: "kit", component: KitView, meta: { private: true } }];
