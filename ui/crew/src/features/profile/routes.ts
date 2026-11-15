import type { RouteRecordRaw } from "vue-router";
import { STATION_ROUTE } from "../identity/sessionLock";
import ProfileView from "./ProfileView.vue";
import SignIn from "./SignIn.vue";

export const routes: RouteRecordRaw[] = [
  { path: "/", name: STATION_ROUTE, component: SignIn, meta: { stage: false } },
  { path: "/profile", name: "profile", component: ProfileView, meta: { private: true, stage: false } },
];
