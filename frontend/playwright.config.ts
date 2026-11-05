import { defineConfig } from "@playwright/test";

/** Key journey against a running stack (API on :8000 with a worker, web on :3000). */
export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  use: { baseURL: process.env.SIMP_WEB_URL ?? "http://localhost:3000", trace: "off", viewport: { width: 1440, height: 900 } },
  reporter: [["list"]],
});
