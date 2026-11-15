import { createRouter, type Router, type RouterHistory, type RouteRecordRaw } from "vue-router";
import { STATION_ROUTE } from "../features/identity/sessionLock";
import type { SessionStore } from "./session";

declare module "vue-router" {
  interface RouteMeta {
    /** The route shows personal content: it mounts only inside an open session. */
    private?: boolean;
  }
}

const features = import.meta.glob<RouteRecordRaw[]>("../features/*/routes.ts", {
  eager: true,
  import: "routes",
});

/**
 * Feature routes plus the private-route guard (E03 7.2). Before every navigation the session deadline is
 * re-checked; a private route without an open session is replaced by the station screen, so neither a direct
 * address nor Back can mount one.
 */
export function createCrewRouter(session: SessionStore, deadline: () => boolean, history: RouterHistory): Router {
  const routes = Object.keys(features)
    .sort()
    .flatMap((key) => features[key] ?? []);
  const router = createRouter({
    history,
    routes: [...routes, { path: "/:unknown(.*)*", redirect: { name: STATION_ROUTE } }],
  });
  router.beforeEach((to) => {
    deadline();
    if (to.meta.private === true && session.current === null) {
      return { name: STATION_ROUTE, replace: true };
    }
    return true;
  });
  return router;
}
