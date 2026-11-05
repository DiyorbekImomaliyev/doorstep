import { defineConfig } from "@playwright/test";

// E2E against a running app (make dev, or the deployed URL via E2E_BASE_URL).
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  // The deployed app calls the model inline (explainer, claim pack): a report can take several
  // seconds, more when it falls back to the second provider. 5 s is too tight there.
  expect: { timeout: 20_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    viewport: { width: 1360, height: 900 },
  },
  reporter: [["list"]],
});
