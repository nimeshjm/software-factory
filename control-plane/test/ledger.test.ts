import { describe, expect, it } from "vitest";
import { buildStatus, failureAlert } from "../src/ledger";
import { computeQuota } from "../src/quota";
import type { RunRow } from "../src/types";

const run = (o: Partial<RunRow>): RunRow => ({
  id: "r", agent: "builder", phase: null, subject: "issue:1", trigger: null, issue_number: null, pr_number: null,
  dispatched_at: "2026-10-21T01:00:00Z", started_at: null, finished_at: null, status: "dispatched",
  stop_reason: null, model_calls: 0, reserved_calls: 0, workflow_run_url: null, ...o,
});
const now = new Date("2026-10-21T12:00:00Z");

describe("quota/status math", () => {
  it("sums today's finished calls and in-flight reservations", () => {
    const rows = [
      run({ id: "a", status: "success", finished_at: "2026-10-21T02:00:00Z", model_calls: 30 }),
      run({ id: "b", status: "failed", finished_at: "2026-10-21T03:00:00Z", model_calls: 5 }),
      run({ id: "c", status: "success", finished_at: "2026-10-20T23:59:00Z", model_calls: 99 }),
      run({ id: "d", status: "started", reserved_calls: 100 }),
      run({ id: "e", status: "dispatched", reserved_calls: 20 }),
      run({ id: "f", status: "lost", finished_at: "2026-10-21T04:00:00Z", model_calls: 0, reserved_calls: 100 }),
    ];
    expect(computeQuota(rows, now)).toEqual({ usedToday: 35, reservedInFlight: 120 });
    const s = buildStatus(rows, true, JSON.stringify({ at: "x", decisions: [1] }), 400, now);
    expect(s.paused).toBe(true);
    expect(s.today).toEqual({ model_calls: 35, reserved: 120, quota: 400 });
    expect(s.in_flight.map((r) => r.run_id)).toEqual(["d", "e"]);
    expect(s.last_tick).toEqual({ at: "x", decisions: [1] });
    expect(buildStatus([], false, null, 400, now).last_tick).toBeNull();
  });
});

describe("failureAlert", () => {
  const row = { agent: "reviewer", subject: "pr:111", workflow_run_url: "https://github.com/o/r/actions/runs/1" };
  it("alerts failed and timeout runs with agent, subject, reason and link", () => {
    expect(failureAlert("r1", { status: "failed", stop_reason: "exception" }, row))
      .toBe("factory: run r1 [reviewer pr:111] failed (exception) https://github.com/o/r/actions/runs/1");
    expect(failureAlert("r2", { status: "timeout" }, null)).toBe("factory: run r2 timeout");
  });
  it("stays quiet for success, denied and lost", () => {
    for (const status of ["success", "denied", "lost"]) expect(failureAlert("r", { status }, row)).toBeNull();
  });
});
