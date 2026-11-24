import { afterEach, describe, expect, it, vi } from "vitest";
import { billingBlockAlert, billingPauseAlert, buildStatus, handleStarted, handleFinished, failureAlert, finishedEvent, startedEvent } from "../src/ledger";
import { sendEvent } from "../src/honeycomb";
import { computePaidBudget, computeQuota, exhaustionWindowStart, paidAllowance, paidExhausted } from "../src/quota";
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
    expect(s.today).toEqual({ model_calls: 35, reserved: 120, quota: 400, paid_usd: 0, paid_reserved_usd: 0, paid_cap_usd: 0, paid_blocked: false });
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
      paid_cost_usd: 0, free_key: null,
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


describe("paid budget", () => {
  const t = new Date("2026-11-24T12:00:00Z");
  const rows = [
    run({ id: "a", status: "success", finished_at: "2026-11-24T02:00:00Z", paid_cost_usd: 0.05 }),
    run({ id: "b", status: "failed", finished_at: "2026-11-24T09:00:00Z", paid_cost_usd: 0.03, stop_reason: "paid_budget" }),
    run({ id: "c", status: "success", finished_at: "2026-11-23T23:00:00Z", paid_cost_usd: 9 }),
    run({ id: "d", status: "started", paid_reserved_usd: 0.04 }),
    run({ id: "me", status: "started", paid_reserved_usd: 0.12 }),
  ];
  it("sums today's spend and other runs' reservations", () => {
    const b = computePaidBudget(rows, t, 0.22, 0.12, false, "me");
    expect(b).toMatchObject({ spentToday: 0.08, reservedInFlight: 0.04, exhausted: true });
    expect(paidAllowance(b)).toBe(0.1); // 0.22 - 0.08 - 0.04
    expect(paidAllowance(computePaidBudget(rows, t, 1, 0.12, false, "me"))).toBe(0.12); // run cap
    expect(paidAllowance(computePaidBudget(rows, t, 0.05, 0.12, false, "me"))).toBe(0);
    expect(paidAllowance(computePaidBudget(rows, t, 1, 0.12, true, "me"))).toBe(0); // blocked
    expect(paidAllowance(computePaidBudget([], t, 0, 0, false))).toBe(0); // no cap configured
  });
  it("is exhausted only after a paid_budget stop since the free reset and with no budget left", () => {
    expect(exhaustionWindowStart(new Date("2026-11-24T03:00:00Z"))).toBe("2026-11-24T00:00:00.000Z");
    expect(exhaustionWindowStart(t)).toBe("2026-11-24T07:00:00.000Z");
    expect(paidExhausted(computePaidBudget(rows, t, 1, 0.12, false))).toBe(false); // budget left
    expect(paidExhausted(computePaidBudget(rows, t, 0.08, 0.12, false))).toBe(true);
    const early = [run({ id: "x", status: "failed", finished_at: "2026-11-24T05:00:00Z", stop_reason: "paid_budget" })];
    expect(paidExhausted(computePaidBudget(early, t, 0, 0, false))).toBe(false); // before the free reset
  });
});

describe("billing alerts with and without a free key", () => {
  it("pauses without a free key, blocks the paid key with one", () => {
    expect(billingPauseAlert("r1", { stop_reason: "billing_blocked", free_key: true }, null)).toBeNull();
    expect(billingBlockAlert("r1", { stop_reason: "billing_blocked", free_key: true }, null, "2026-11"))
      .toBe("factory: paid Gemini key blocked for 2026-11 on run r1 (prepay balance or spend cap); running on the free key only. Top up or raise the cap, then POST /admin/resume to use it again.");
    expect(billingBlockAlert("r1", { stop_reason: "billing_blocked" }, null, "2026-11")).toBeNull();
    expect(billingBlockAlert("r1", { stop_reason: "exception", free_key: true }, null, "2026-11")).toBeNull();
  });
});

describe("handleStarted and handleFinished paid fields", () => {
  const fakeEnv = (rows: Partial<RunRow>[], config: Record<string, string> = {}) => {
    const writes: { sql: string; args: unknown[] }[] = [];
    const stmt = (sql: string, args: unknown[] = []) => ({
      bind: (...a: unknown[]) => stmt(sql, a),
      run: async () => { writes.push({ sql, args }); return {}; },
      first: async () => {
        const m = /key = '([a-z_]+)'/.exec(sql);
        return m && config[m[1]] !== undefined ? { value: config[m[1]] } : null;
      },
      all: async () => ({ results: rows.map((r) => run(r)) }),
    });
    return { env: { DB: { prepare: (sql: string) => stmt(sql) }, DAILY_PAID_CAP_USD: "0.22", RUN_PAID_CAP_USD: "0.12" } as never, writes };
  };
  const post = (path: string, body: unknown) => new Request(`https://x/runs/r1/${path}`, { method: "POST", body: JSON.stringify(body) });

  it("started returns and reserves the allowance", async () => {
    const { env, writes } = fakeEnv([{ id: "o", status: "success", finished_at: new Date().toISOString(), paid_cost_usd: 0.15 }]);
    const res = await handleStarted(post("started", { agent: "builder" }), env, { waitUntil() {} } as never, "r1");
    const body = (await res.json()) as { paid_allowance_usd: number };
    expect(body.paid_allowance_usd).toBe(0.07);
    expect(writes.find((w) => w.sql.startsWith("UPDATE runs SET paid_reserved_usd"))?.args).toEqual([0.07, "r1"]);
  });
  it("started gives nothing while the paid key is blocked this month", async () => {
    const month = new Date().toISOString().slice(0, 7);
    const { env } = fakeEnv([], { paid_blocked_month: month });
    const res = await handleStarted(post("started", {}), env, { waitUntil() {} } as never, "r1");
    expect(((await res.json()) as { paid_allowance_usd: number }).paid_allowance_usd).toBe(0);
  });
  it("finished stores the paid cost; billing_blocked with a free key blocks the month instead of pausing", async () => {
    const { env, writes } = fakeEnv([]);
    await handleFinished(post("finished", { status: "failed", stop_reason: "billing_blocked", paid_cost_usd: 0.031, free_key: true }),
      env, { waitUntil() {} } as never, "r1");
    const update = writes.find((w) => w.sql.startsWith("UPDATE runs SET status"));
    expect(update?.args.at(-2)).toBe(0.031);
    expect(writes.some((w) => w.sql.includes("'paid_blocked_month'"))).toBe(true);
    expect(writes.some((w) => w.sql.includes("'paused', '1'"))).toBe(false);
  });
});
