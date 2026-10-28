import { agentSpec, AGENT_SPECS, dailyQuota, maxOpenFactoryPrs } from "./config";
import { dispatchRun } from "./dispatch";
import { listIssues, listOpenPRs } from "./github";
import { sendAlert } from "./alert";
import { sendEvent } from "./honeycomb";
import { planTick, type Decision, type TickState } from "./preflight";
import { isPaused, loadQuota } from "./quota";
import type { Env, RunRow } from "./types";

export async function loadTickState(env: Env, now: Date): Promise<TickState> {
  const paused = await isPaused(env);
  const quota = await loadQuota(env, now);
  const since = new Date(now.getTime() - 26 * 3600_000).toISOString();
  const [inflight, recent] = await Promise.all([
    env.DB.prepare("SELECT id, agent, subject, dispatched_at, reserved_calls FROM runs WHERE status IN ('dispatched','started')").all<RunRow>(),
    env.DB.prepare("SELECT agent, subject, dispatched_at FROM runs WHERE dispatched_at >= ?").bind(since).all<RunRow>(),
  ]);
  const base: TickState = {
    paused,
    quota: { daily: dailyQuota(env), ...quota },
    inFlight: (inflight.results ?? []).map((r) => ({
      runId: r.id,
      agent: r.agent,
      subject: r.subject,
      dispatchedAt: r.dispatched_at,
      timeoutMinutes: agentSpec(r.agent).timeoutMinutes,
      reservedCalls: r.reserved_calls,
    })),
    recentRuns: (recent.results ?? []).map((r) => ({ agent: r.agent, subject: r.subject, dispatchedAt: r.dispatched_at })),
    openPRs: [],
    openIssues: [],
    agentSpecs: AGENT_SPECS,
    maxOpenFactoryPrs: maxOpenFactoryPrs(env),
    unlabeledIssues: [],
  };
  if (paused) return base; // skip GitHub calls entirely while paused
  // 2 list calls (plus token caching) keeps the tick well under 50 subrequests.
  const [prs, issues] = await Promise.all([listOpenPRs(env, env.TARGET_REPO), listIssues(env, env.TARGET_REPO, { state: "open" })]);
  base.openPRs = prs.map((p) => ({ number: p.number, headRef: p.headRef, body: p.body, author: p.author, labels: p.labels }));
  base.openIssues = issues.map((i) => ({ number: i.number, labels: i.labels, authorAssociation: i.authorAssociation, body: i.body }));
  base.unlabeledIssues = issues.filter((i) => i.labels.length === 0).map((i) => i.number);
  return base;
}

export function summarize(d: Decision): Record<string, unknown> {
  if (d.kind === "dispatch") return { kind: d.kind, agent: d.agent, subject: d.subject, run_id: d.inputs.run_id, reserved: d.reservedCalls };
  if (d.kind === "mark_lost") return { kind: d.kind, run_id: d.runId };
  return { kind: d.kind, agent: d.agent, reason: d.reason };
}

export async function runTick(env: Env, ctx: ExecutionContext, now: Date = new Date()): Promise<Decision[]> {
  let decisions: Decision[] = [];
  const results: Record<string, unknown>[] = [];
  try {
    const state = await loadTickState(env, now);
    decisions = planTick(state, now);
    for (const d of decisions) {
      let outcome: Record<string, unknown> = summarize(d);
      if (d.kind === "mark_lost") {
        await env.DB.prepare("UPDATE runs SET status = 'lost', stop_reason = 'timeout_no_report', finished_at = ? WHERE id = ? AND status IN ('dispatched','started')")
          .bind(now.toISOString(), d.runId)
          .run();
        await env.DB.prepare("DELETE FROM locks WHERE run_id = ?").bind(d.runId).run();
        await sendAlert(env, `factory: run ${d.runId} marked lost (no report before timeout)`, ctx);
      } else if (d.kind === "dispatch") {
        const r = await dispatchRun(env, { agent: d.agent, subject: d.subject, inputs: d.inputs, reservedCalls: d.reservedCalls, trigger: "cron" }, ctx, now);
        outcome = { ...outcome, ok: r.ok, ...(r.ok ? {} : { reason: r.reason }) };
      }
      results.push(outcome);
      void sendEvent(env, { name: "tick.decision", ...outcome }, ctx);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    results.push({ kind: "error", error: msg });
    await sendAlert(env, `factory: tick failed: ${msg}`, ctx);
    void sendEvent(env, { name: "tick.error", error: msg }, ctx);
  }
  await env.DB.prepare("INSERT INTO config (key, value) VALUES ('last_tick', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    .bind(JSON.stringify({ at: now.toISOString(), decisions: results }))
    .run();
  return decisions;
}
