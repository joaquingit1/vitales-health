import type { RouteRecordRaw } from "vue-router";
import HelpView from "./HelpView.vue";

/** Someone needs help (E26): the first screen is on screen at once and works with the model stopped. */
export const routes: RouteRecordRaw[] = [{ path: "/help", name: "help", component: HelpView, meta: { private: true } }];
