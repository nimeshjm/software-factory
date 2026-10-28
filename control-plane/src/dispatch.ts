import { sendAlert } from "./alert";
import { dispatchWorkflow } from "./github";
import type { DispatchInputs, Env } from "./types";
import { toInt } from "./util";

export interface DispatchRequest {
  agent: string;
  subject: string;
  inputs: DispatchInputs;
  reservedCalls: number;
  trigger: string;
}
export type DispatchResult = { ok: true; runId: string } | { ok: false; reason: "locked" | "dispatch_failed"; error?: string };

/** Shared lock + ledger + workflow_dispatch path used by the scheduler and webhooks. */
export async function dispatchRun(
  env: Env,
  req: DispatchRequest,
  ctx?: ExecutionContext,
  now: Date = new Date(),
): Promise<DispatchResult> {
  const runId = req.inputs.run_id;
  const ts = now.toISOString();
  const lock = await env.DB.prepare(
    "INSERT OR IGNORE INTO locks (agent, subject, run_id, created_at) VALUES (?, ?, ?, ?)",
  )
    .bind(req.agent, req.subject, runId, ts)
    .run();
  if (!lock.meta.changes) return { ok: false, reason: "locked" };

  try {
    await env.DB.prepare(
      `INSERT INTO runs (id, agent, phase, subject, trigger, issue_number, pr_number, dispatched_at, status, model_calls, reserved_calls)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'dispatched', 0, ?)`,
    )
      .bind(
        runId,
        req.agent,
        req.inputs.phase,
        req.subject,
        req.trigger,
        toInt(req.inputs.issue),
        toInt(req.inputs.pr),
        ts,
        req.reservedCalls,
      )
      .run();
    await dispatchWorkflow(env, env.FACTORY_REPO, `${req.agent}.yml`, { ...req.inputs }, env.FACTORY_REF);
    return { ok: true, runId };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await env.DB.prepare("DELETE FROM locks WHERE agent = ? AND subject = ? AND run_id = ?")
      .bind(req.agent, req.subject, runId)
      .run();
    await env.DB.prepare(
      "UPDATE runs SET status = 'failed', stop_reason = 'dispatch_failed', finished_at = ? WHERE id = ?",
    )
      .bind(ts, runId)
      .run();
    await sendAlert(env, `factory: dispatch of ${req.agent} (${req.subject}) failed: ${msg}`, ctx);
    return { ok: false, reason: "dispatch_failed", error: msg };
  }
}
