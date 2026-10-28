// Converts a Playwright JSON report to the factory's inputs/smoke.json shape.
// Usage: node to-smoke-json.mjs playwright-report.json > ../inputs/smoke.json
import { readFileSync } from "node:fs";

const report = JSON.parse(readFileSync(process.argv[2], "utf8"));
const tests = [];

function walk(suite) {
  for (const spec of suite.specs ?? []) {
    for (const t of spec.tests ?? []) {
      const last = (t.results ?? []).at(-1);
      const s = last?.status ?? t.status ?? "skipped";
      const status = s === "passed" ? "passed" : s === "skipped" ? "skipped" : "failed";
      // eslint-disable-next-line no-control-regex
      const msg = last?.error?.message?.replace(/\u001b\[[0-9;]*m/g, "").split("\n").slice(0, 3).join(" ").slice(0, 500);
      tests.push({ name: spec.title, status, error: status === "failed" ? (msg ?? "failed") : null });
    }
  }
  for (const s of suite.suites ?? []) walk(s);
}
for (const s of report.suites ?? []) walk(s);

const passed = tests.length > 0 && tests.every((t) => t.status !== "failed");
process.stdout.write(JSON.stringify({ passed, tests }, null, 2) + "\n");
