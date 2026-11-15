import type { RouteRecordRaw } from "vue-router";
import TodayView from "./TodayView.vue";

export const routes: RouteRecordRaw[] = [{ path: "/today", name: "today", component: TodayView, meta: { private: true } }];
