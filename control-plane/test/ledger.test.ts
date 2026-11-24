import { afterEach, describe, expect, it, vi } from "vitest";
import { billingPauseAlert, buildStatus, handleFinished, failureAlert, finishedEvent, startedEvent } from "../src/ledger";
import { sendEvent } from "../src/honeycomb";
import { computeQuota } from "../src/quota";
import type { RunRow } from "../src/types";

const run = (o: Partial<RunRow>): RunRow => ({
  id: "r", agent: "builder", phase: null, subject: "issue:1", trigger: null, issue_number: null, pr_number: null,
  dispatched_at: "2026-10-21T01:00:00Z", started_at: null, finished_at: null, status: "dispatched",
  stop_reason: null, error: null, model_calls: 0, reserved_calls: 0, workflow_run_url: null, ...o,
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

describe("billingPauseAlert", () => {
  it("names the run, the cause and how to resume, only for billing_blocked", () => {
    expect(billingPauseAlert("r1", { stop_reason: "billing_blocked" }, { workflow_run_url: "https://example/run/1" }))
      .toBe("factory: PAUSED - Gemini billing blocked on run r1 (prepay balance or spend cap). Top up or raise the cap, then POST /admin/resume. https://example/run/1");
    expect(billingPauseAlert("r1", { stop_reason: "exception" }, null)).toBeNull();
    expect(billingPauseAlert("r1", {}, null)).toBeNull();
  });
});

describe("finishedEvent", () => {
  const started = run({
    id: "builder-202610210100", phase: "implement", trigger: "cron", issue_number: 7, pr_number: 9, model_calls: 12, reserved_calls: 100,
    dispatched_at: "2026-10-21T11:00:00Z", started_at: "2026-10-21T11:00:45Z", workflow_run_url: "https://example/run/1",
  });
  const at = new Date("2026-10-21T11:10:00.900Z");
  it("sends the whole flat field set", () => {
    const e = finishedEvent(started, {
      status: "failed", stop_reason: "exception", usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 },
      error: "boom", throttles: 3, dry_run: false,
    }, at);
    expect(e).toEqual({
      name: "run.finished", run_id: "builder-202610210100", agent: "builder", subject: "issue:1", trigger: "cron",
      status: "failed", stop_reason: "exception", phase: "implement", error: "boom", model_calls: 12, reserved_calls: 100,
      throttles: 3, "usage.input_tokens": 100, "usage.output_tokens": 20, "usage.total_tokens": 120,
      duration_s: 555, queue_s: 45, issue_number: 7, pr_number: 9, workflow_run_url: "https://example/run/1", dry_run: false, verdict: null,
    });
  });
  it("gives null durations for a run that never started and nulls for absent fields", () => {
    const e = finishedEvent(run({ id: "x" }), { status: "timeout" }, at);
    expect(e.duration_s).toBeNull();
    expect(e.queue_s).toBeNull();
    for (const k of ["stop_reason", "phase", "error", "throttles", "usage.input_tokens", "usage.output_tokens", "usage.total_tokens", "dry_run", "verdict", "trigger", "workflow_run_url"]) {
      expect(e).toHaveProperty(k, null);
    }
  });
  it("clips error to 300 chars", () => {
    expect((finishedEvent(started, { status: "failed", error: "x".repeat(500) }, at).error as string).length).toBe(300);
  });
  it("prefers body.phase over row.phase", () => {
    expect(finishedEvent(started, { status: "failed", phase: "plan" }, at).phase).toBe("plan");
    expect(finishedEvent(started, { status: "failed" }, at).phase).toBe("implement");
  });
  it("keeps only known verdicts", () => {
    expect(finishedEvent(started, { status: "success", verdict: "request_changes" }, at).verdict).toBe("request_changes");
    expect(finishedEvent(started, { status: "success", verdict: "maybe" }, at).verdict).toBeNull();
    expect(finishedEvent(started, { status: "success" }, at).verdict).toBeNull();
  });
  it("has the lost shape", () => {
    const e = finishedEvent(started, { status: "lost", stop_reason: "timeout_no_report" }, at);
    expect(e).toMatchObject({ status: "lost", stop_reason: "timeout_no_report", phase: "implement", error: null, throttles: null, dry_run: null, model_calls: 12 });
    expect(e["usage.total_tokens"]).toBeNull();
  });
});

describe("startedEvent", () => {
  const row = run({ id: "r1", trigger: "cron", phase: "plan", dispatched_at: "2026-10-21T11:00:00Z", workflow_run_url: "https://row" });
  const at = new Date("2026-10-21T11:00:30.500Z");
  it("takes phase and url from the body, falling back to the row", () => {
    expect(startedEvent(row, { phase: "implement", workflow_run_url: "https://body" }, at)).toEqual({
      name: "run.started", run_id: "r1", agent: "builder", subject: "issue:1", phase: "implement", trigger: "cron", workflow_run_url: "https://body", queue_s: 30,
    });
    expect(startedEvent(row, {}, at)).toMatchObject({ phase: "plan", workflow_run_url: "https://row" });
  });
});

describe("sendEvent", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("posts flat JSON with the service field to the dataset", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const env = { HONEYCOMB_API_KEY: "k", HONEYCOMB_DATASET: "factory" } as never;
    await sendEvent(env, { name: "run.finished", "usage.input_tokens": 5, error: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.eu1.honeycomb.io/1/events/factory");
    expect(init.method).toBe("POST");
    expect(init.headers["x-honeycomb-team"]).toBe("k");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ service: "factory-control-plane", name: "run.finished", "usage.input_tokens": 5, error: null });
  });
});

describe("handleFinished", () => {
  const call = async (body: Record<string, unknown>) => {
    const binds: unknown[][] = [];
    const stmt = (sql: string) => ({
      bind: (...a: unknown[]) => { if (sql.startsWith("UPDATE runs")) binds.push([sql, ...a]); return stmt(sql); },
      run: async () => ({}),
      first: async () => null,
    });
    const env = { DB: { prepare: stmt } } as never;
    const req = new Request("https://x/runs/r1/finished", { method: "POST", body: JSON.stringify(body) });
    const res = await handleFinished(req, env, { waitUntil() {} } as never, "r1");
    expect(res.status).toBe(200);
    return binds[0];
  };

  it("pauses the factory on billing_blocked and only then", async () => {
    const sqls: string[] = [];
    const stmt = (sql: string) => ({ bind: () => stmt(sql), run: async () => { sqls.push(sql); return {}; }, first: async () => null });
    const env = { DB: { prepare: stmt } } as never;
    for (const stop_reason of ["billing_blocked", "exception"]) {
      const req = new Request("https://x/runs/r1/finished", { method: "POST", body: JSON.stringify({ status: "failed", stop_reason }) });
      expect((await handleFinished(req, env, { waitUntil() {} } as never, "r1")).status).toBe(200);
    }
    expect(sqls.filter((q) => q.includes("'paused', '1'"))).toHaveLength(1);
  });

  it("stores the error clipped to 300 chars, null when absent", async () => {
    const withErr = await call({ status: "failed", stop_reason: "end_turn", error: "e".repeat(400) });
    expect(withErr[1]).toBe("failed");
    expect(withErr[3]).toBe("e".repeat(300));
    const without = await call({ status: "success", stop_reason: "end_turn" });
    expect(without[3]).toBeNull();
  });
});
