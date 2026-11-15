import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const hub = process.env["CREW_HUB_ORIGIN"] ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/v1": { target: hub },
    },
  },
});
