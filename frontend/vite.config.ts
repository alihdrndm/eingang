import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8010";

const proxy = {
  "/api": { target: apiProxyTarget },
};

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3110,
    strictPort: true,
    proxy,
  },
  preview: {
    port: 3110,
    strictPort: true,
    proxy,
  },
  test: {
    environment: "jsdom",
    // Playwright's end-to-end specs (e2e/) run in a real browser, not here.
    include: ["src/**/*.test.ts", "middleware.test.ts"],
  },
});
