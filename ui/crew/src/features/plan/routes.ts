import type { RouteRecordRaw } from "vue-router";
import PlanView from "./PlanView.vue";

/** The plan aboard (E28): /plan/ICL10, /plan/plan_icl10 or /plan/<plan_run_id>. */
export const routes: RouteRecordRaw[] = [{ path: "/plan/:id", name: "plan", component: PlanView, meta: { private: true } }];
