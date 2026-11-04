import { defineConfig } from "@playwright/test";

// E2E against a running app (make dev, or the deployed URL via E2E_BASE_URL).
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    viewport: { width: 1360, height: 900 },
  },
  reporter: [["list"]],
});
