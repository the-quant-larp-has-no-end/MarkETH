import { defineConfig } from "@playwright/test";

import { BASE_URL } from "./e2e/support/runner";

/**
 * The browser suite (`npm run e2e`, `make e2e`): the specs of `e2e/` in Chrome, against a
 * stack that is up. It starts nothing and stops nothing.
 *
 *   npm run e2e                                  the development stack, http://localhost:8080
 *   E2E_IDENTITY=headers E2E_BASE_URL=http://127.0.0.1:<port> npm run e2e
 *                                                a stack of compose.yaml alone
 *
 * `e2e/support/runner.ts` lists every variable the runner may set.
 *
 * Each spec runs twice, at the two widths the design is drawn for: the project "phone"
 * (375 × 667) and the project "desktop" (1024 × 768). One worker runs them one after the
 * other: the specs act as people of one database, and the proxy counts each person's
 * requests.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    // The Chrome that is installed on this machine, not a browser Playwright downloads.
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "phone", use: { viewport: { width: 375, height: 667 } } },
    { name: "desktop", use: { viewport: { width: 1024, height: 768 } } },
  ],
});
