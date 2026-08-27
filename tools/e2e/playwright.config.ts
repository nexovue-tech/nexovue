import { defineConfig, devices } from "@playwright/test";

import { WEB_ORIGIN } from "./tests/support/stack";

/**
 * P1-60: the end-to-end harness.
 *
 * There is no `webServer` block on purpose. The stack these specs drive is five services
 * and four datastores managed by deploy/compose; Playwright cannot own its lifecycle, so
 * it assumes it is already up and tests/stack.setup.ts fails loudly when it is not.
 */

/** Every spec waits on a real pipeline — an export has to be batched before it is queryable. */
const TEST_TIMEOUT_MS = 60_000;
const ASSERTION_TIMEOUT_MS = 15_000;

/**
 * Traces, screenshots and reports are build output, and build output lives under
 * node_modules here — the same reasoning as `tsBuildInfoFile` in apps/web. Left at
 * Playwright's default the artifacts land in the working tree, where `just check` lints
 * and format-checks them and fails on JSON it did not write.
 */
const ARTIFACTS = "./node_modules/.playwright";

export default defineConfig({
  testDir: "./tests",
  outputDir: `${ARTIFACTS}/results`,
  timeout: TEST_TIMEOUT_MS,
  expect: { timeout: ASSERTION_TIMEOUT_MS },

  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // One retry on CI covers infrastructure noise. Locally a flake must be visible as one.
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: `${ARTIFACTS}/report` }]]
    : [["list"]],

  use: {
    baseURL: WEB_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    { name: "stack", testMatch: /stack\.setup\.ts$/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["stack"],
    },
  ],
});
