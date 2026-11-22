import { dailyQuota } from "./config";
import { computeQuota } from "./quota";
import { isPaused } from "./quota";
import { sendAlert } from "./alert";
import { sendEvent } from "./honeycomb";
import type { Env, RunRow } from "./types";
import { IN_FLIGHT_STATUSES } from "./types";
import { json, startOfUtcDay, toInt } from "./util";

const FINAL = ["success", "failed", "timeout", "denied", "lost"];
// Statuses worth a human's attention. "denied" (author gate) is expected; "lost" is alerted by the tick.
const ALERT_STATUSES = new Set(["failed", "timeout"]);

/** Pure: the alert text for a finished run, or null when the outcome needs no alert. */
export function failureAlert(
  id: string,
  b: { status: string; stop_reason?: string | null },
  row: { agent?: string | null; subject?: string | null; workflow_run_url?: string | null } | null,
): string | null {
  if (!ALERT_STATUSES.has(b.status)) return null;
  const who = [row?.agent, row?.subject].filter(Boolean).join(" ");
  const reason = b.stop_reason ? ` (${b.stop_reason})` : "";
  const link = row?.workflow_run_url ? ` ${row.workflow_run_url}` : "";
  return `factory: run ${id}${who ? ` [${who}]` : ""} ${b.status}${reason}${link}`;
}

async function ensureRun(env: Env, id: string, agent: string, phase: string | null, now: string): Promise<void> {
  // Manually dispatched runs report to the ledger without a prior row.
  await env.DB.prepare(
    "INSERT OR IGNORE INTO runs (id, agent, phase, subject, trigger, dispatched_at, status, model_calls, reserved_calls) VALUES (?, ?, ?, ?, 'manual', ?, 'dispatched', 0, 0)",
  )
    .bind(id, agent || "unknown", phase, `manual:${id}`, now)
    .run();
}

export interface FinishedBody {
  status?: string;
  stop_reason?: string | null;
  usage?: unknown;
  phase?: string | null;
  error?: string | null;
  throttles?: number | null;
  dry_run?: boolean | null;
  verdict?: string | null;
}

const VERDICTS = new Set(["approve", "request_changes", "comment", "needs_human"]);
const intOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? Math.floor(v) : null);
const secondsBetween = (from: string | null, to: Date | string | null): number | null => {
  if (!from || !to) return null;
  const a = Date.parse(from);
  const b = typeof to === "string" ? Date.parse(to) : to.getTime();
  return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.floor((b - a) / 1000)) : null;
};

/** Pure: the `run.started` Honeycomb event (see CONTRACTS.md, Honeycomb events). */
export function startedEvent(row: RunRow, body: { phase?: string | null; workflow_run_url?: string | null }, now: Date): Record<string, unknown> {
  return {
    name: "run.started",
    run_id: row.id,
    agent: row.agent,
    subject: row.subject,
    phase: body.phase ?? row.phase ?? null,
    trigger: row.trigger ?? null,
    workflow_run_url: body.workflow_run_url ?? row.workflow_run_url ?? null,
    queue_s: secondsBetween(row.dispatched_at, now),
  };
}

/** Pure: the `run.finished` Honeycomb event from the run row (read after the update) and the report body. */
export function finishedEvent(row: RunRow, body: FinishedBody, now: Date): Record<string, unknown> {
  const u = (body.usage && typeof body.usage === "object" ? body.usage : {}) as Record<string, unknown>;
  const started = row.started_at;
  return {
    name: "run.finished",
    run_id: row.id,
    agent: row.agent,
    subject: row.subject,
    trigger: row.trigger ?? null,
    status: body.status ?? row.status,
    stop_reason: body.stop_reason ?? null,
    phase: body.phase ?? row.phase ?? null,
    error: typeof body.error === "string" ? body.error.slice(0, 300) : null,
    model_calls: row.model_calls,
    reserved_calls: row.reserved_calls,
    throttles: intOrNull(body.throttles),
    "usage.input_tokens": intOrNull(u.input_tokens),
    "usage.output_tokens": intOrNull(u.output_tokens),
    "usage.total_tokens": intOrNull(u.total_tokens),
    duration_s: started ? secondsBetween(started, now) : null,
    queue_s: started ? secondsBetween(row.dispatched_at, started) : null,
    issue_number: row.issue_number ?? null,
    pr_number: row.pr_number ?? null,
    workflow_run_url: row.workflow_run_url ?? null,
    dry_run: typeof body.dry_run === "boolean" ? body.dry_run : null,
    verdict: typeof body.verdict === "string" && VERDICTS.has(body.verdict) ? body.verdict : null,
  };
}

export async function handleStarted(req: Request, env: Env, ctx: ExecutionContext, id: string): Promise<Response> {
  const b = (await req.json().catch(() => null)) as { agent?: string; phase?: string; workflow_run_url?: string } | null;
  if (!b) return json({ error: "bad json" }, 400);
  const nowDate = new Date();
  const now = nowDate.toISOString();
  await ensureRun(env, id, b.agent ?? "", b.phase ?? null, now);
  await env.DB.prepare("UPDATE runs SET started_at = ?, status = 'started', workflow_run_url = ?, phase = COALESCE(?, phase) WHERE id = ? AND status IN ('dispatched','started')")
    .bind(now, b.workflow_run_url ?? null, b.phase ?? null, id)
    .run();
  const row = await env.DB.prepare("SELECT * FROM runs WHERE id = ?").bind(id).first<RunRow>();
  if (row) void sendEvent(env, startedEvent(row, b, nowDate), ctx);
  return json({ ok: true });
}

export async function handleFinished(req: Request, env: Env, ctx: ExecutionContext, id: string): Promise<Response> {
  const b = (await req.json().catch(() => null)) as (FinishedBody & {
    model_calls?: number; pr_number?: number | null; issue_number?: number | null; agent?: string;
  }) | null;
  if (!b || !b.status || !FINAL.includes(b.status)) return json({ error: "bad status" }, 400);
  const nowDate = new Date();
  const now = nowDate.toISOString();
  await ensureRun(env, id, b.agent ?? "", null, now);
  const calls = Number.isFinite(b.model_calls) ? Math.max(0, Math.floor(b.model_calls as number)) : 0;
  await env.DB.prepare(
    "UPDATE runs SET status = ?, stop_reason = ?, model_calls = ?, finished_at = ?, pr_number = COALESCE(?, pr_number), issue_number = COALESCE(?, issue_number) WHERE id = ?",
  )
    .bind(b.status, b.stop_reason ?? null, calls, now, toInt(b.pr_number), toInt(b.issue_number), id)
    .run();
  await env.DB.prepare("DELETE FROM locks WHERE run_id = ?").bind(id).run();
  const row = await env.DB.prepare("SELECT * FROM runs WHERE id = ?").bind(id).first<RunRow>();
  if (row) void sendEvent(env, finishedEvent(row, b, nowDate), ctx);
  if (ALERT_STATUSES.has(b.status)) {
    const text = failureAlert(id, { status: b.status, stop_reason: b.stop_reason }, row);
    if (text) void sendAlert(env, text, ctx);
  }
  return json({ ok: true });
}

export interface StatusDoc {
  paused: boolean;
  today: { model_calls: number; reserved: number; quota: number };
  in_flight: { run_id: string; agent: string; subject: string; status: string; dispatched_at: string; started_at: string | null; workflow_run_url: string | null }[];
  last_tick: { at: string; decisions: unknown[] } | null;
}

/** Pure status math. */
export function buildStatus(runs: RunRow[], paused: boolean, lastTick: string | null, daily: number, now: Date): StatusDoc {
  const q = computeQuota(runs, now);
  let last: StatusDoc["last_tick"] = null;
  try {
    last = lastTick ? JSON.parse(lastTick) : null;
  } catch {
    last = null;
  }
  return {
    paused,
    today: { model_calls: q.usedToday, reserved: q.reservedInFlight, quota: daily },
    in_flight: runs
      .filter((r) => (IN_FLIGHT_STATUSES as string[]).includes(r.status))
      .map((r) => ({ run_id: r.id, agent: r.agent, subject: r.subject, status: r.status, dispatched_at: r.dispatched_at, started_at: r.started_at, workflow_run_url: r.workflow_run_url })),
    last_tick: last,
  };
}

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export function statusHtml(s: StatusDoc): string {
  const rows = (items: string[][]) => items.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
  return `<!doctype html><meta charset="utf-8"><title>Factory status</title><style>body{font:14px system-ui;margin:2rem}td,th{border:1px solid #888;padding:4px 8px}table{border-collapse:collapse;margin-bottom:1rem}</style>
<h1>Factory ${s.paused ? "(PAUSED)" : ""}</h1>
<p>Model calls today: ${s.today.model_calls} used + ${s.today.reserved} reserved / ${s.today.quota}</p>
<h2>In flight</h2><table><tr><th>run</th><th>agent</th><th>subject</th><th>status</th><th>dispatched</th></tr>${rows(s.in_flight.map((r) => [r.run_id, r.agent, r.subject, r.status, r.dispatched_at]))}</table>
<h2>Last tick ${esc(s.last_tick?.at ?? "never")}</h2><table><tr><th>decision</th></tr>${rows((s.last_tick?.decisions ?? []).map((d) => [JSON.stringify(d)]))}</table>`;
}

export async function handleStatus(req: Request, env: Env): Promise<Response> {
  const now = new Date();
  const { results } = await env.DB.prepare("SELECT * FROM runs WHERE status IN ('dispatched','started') OR finished_at >= ?")
    .bind(startOfUtcDay(now))
    .all<RunRow>();
  const lt = await env.DB.prepare("SELECT value FROM config WHERE key = 'last_tick'").first<{ value: string }>();
  const doc = buildStatus(results ?? [], await isPaused(env), lt?.value ?? null, dailyQuota(env), now);
  if ((req.headers.get("accept") ?? "").includes("text/html")) {
    return new Response(statusHtml(doc), { headers: { "content-type": "text/html; charset=utf-8" } });
  }
  return json(doc);
}
