import { defineConfig } from "@playwright/test";

// No traces, screenshots or videos: nothing from the target is persisted.
// The JSON report is converted to inputs/smoke.json by to-smoke-json.mjs.
export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  retries: 1,
  workers: 1,
  reporter: [["json", { outputFile: "playwright-report.json" }]],
  use: { trace: "off", screenshot: "off", video: "off" },
});
