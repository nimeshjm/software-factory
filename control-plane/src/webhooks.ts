import { dispatchRun } from "./dispatch";
import { agentSpec, dailyQuota } from "./config";
import { sendEvent } from "./honeycomb";
import { makeRunId } from "./preflight";
import { isPaused, loadQuota } from "./quota";
import type { Env } from "./types";
import { json, safeEqual, toHex } from "./util";

const enc = new TextEncoder();
export const DEBOUNCE_MS = 5 * 60_000;
export const SECURITY_DEBOUNCE_MS = 60 * 60_000;

export async function hmacHex(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return toHex(await crypto.subtle.sign("HMAC", key, enc.encode(body)));
}

export async function verifySignature(secret: string, body: string, header: string | null): Promise<boolean> {
  if (!secret || !header || !header.startsWith("sha256=")) return false;
  return safeEqual(header.slice(7), await hmacHex(secret, body));
}

export function isDroppedSender(event: string, payload: any): boolean {
  if (event === "check_suite" || event === "workflow_run" || event === "code_scanning_alert" || event === "dependabot_alert") return false;
  return String(payload?.sender?.login ?? "").endsWith("[bot]");
}

export interface Intent {
  agent: string;
  subject: string;
  phase: string;
  issue: string;
  pr: string;
}

export interface DeployTrigger {
  workflowName: string;
  branch: string;
}

export function mapEvent(event: string, payload: any, targetRepo: string, deploy: DeployTrigger): Intent[] {
  const repo = String(payload?.repository?.full_name ?? "");
  if (repo.toLowerCase() !== targetRepo.toLowerCase()) return [];
  const action = payload?.action;
  if (event === "issues" && action === "opened" && payload.issue?.number) {
    const n = payload.issue.number;
    return [{ agent: "triage", subject: `issue:${n}`, phase: "triage", issue: String(n), pr: "" }];
  }
  if (event === "pull_request" && (action === "opened" || action === "synchronize") && payload.pull_request?.number) {
    const n = payload.pull_request.number;
    return [{ agent: "reviewer", subject: `pr:${n}`, phase: "review", issue: "", pr: String(n) }];
  }
  if (event === "check_suite" && action === "completed") {
    const prs: any[] = payload.check_suite?.pull_requests ?? [];
    return prs.slice(0, 1).map((p) => ({ agent: "reviewer", subject: `pr:${p.number}`, phase: "review", issue: "", pr: String(p.number) }));
  }
  if (event === "workflow_run" && action === "completed") {
    const w = payload.workflow_run;
    if (w?.name === deploy.workflowName && w.head_branch === deploy.branch && w.conclusion === "success" && w.head_sha) {
      return [{ agent: "verifier", subject: `deploy:${w.head_sha}`, phase: "verify", issue: "", pr: "" }];
    }
  }
  // One constant subject: the per-(agent, subject) lock and the debounce cover every alert event together.
  if (
    (event === "code_scanning_alert" && (action === "created" || action === "appeared_in_branch")) ||
    (event === "dependabot_alert" && action === "created")
  ) {
    return [{ agent: "security-triage", subject: "security", phase: "triage", issue: "", pr: "" }];
  }
  return [];
}

/** Pure: true when a run for the subject is in flight or finished within the window (reviewer 5 min, security-triage 60 min). */
export function isDebounced(rows: { status: string; finished_at: string | null }[], now: Date, windowMs = DEBOUNCE_MS): boolean {
  return rows.some((r) => {
    if (r.status === "dispatched" || r.status === "started") return true;
    return !!r.finished_at && now.getTime() - Date.parse(r.finished_at) < windowMs;
  });
}

export async function handleWebhook(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const body = await req.text();
  if (!(await verifySignature(env.GITHUB_WEBHOOK_SECRET, body, req.headers.get("x-hub-signature-256")))) {
    return json({ error: "bad signature" }, 401);
  }
  const event = req.headers.get("x-github-event") ?? "";
  const delivery = req.headers.get("x-github-delivery");
  if (!delivery) return json({ error: "missing delivery id" }, 400);
  let payload: any;
  try {
    payload = JSON.parse(body);
  } catch {
    return json({ error: "bad json" }, 400);
  }
  const now = new Date();
  const ins = await env.DB.prepare("INSERT OR IGNORE INTO events (delivery_id, received_at, event, action) VALUES (?, ?, ?, ?)")
    .bind(delivery, now.toISOString(), event, payload.action ?? null)
    .run();
  if (!ins.meta.changes) return json({ duplicate: true });
  if (event === "ping") return json({ pong: true });
  if (isDroppedSender(event, payload)) return json({ ignored: "bot sender" });

  const intents = mapEvent(event, payload, env.TARGET_REPO, {
    workflowName: env.DEPLOY_WORKFLOW_NAME,
    branch: env.TARGET_DEFAULT_BRANCH,
  });
  const outcomes: Record<string, unknown>[] = [];
  let failed = false;
  for (const it of intents) {
    const o: Record<string, unknown> = { agent: it.agent, subject: it.subject };
    if (payload.alert?.number !== undefined) o.alert = payload.alert.number;
    if (await isPaused(env)) o.skipped = "paused";
    else {
      const spec = agentSpec(it.agent);
      const q = await loadQuota(env, now);
      if (q.usedToday + q.reservedInFlight + spec.maxModelCalls > dailyQuota(env)) o.skipped = "quota";
      else {
        let debounced = false;
        if (it.agent === "reviewer" || it.agent === "security-triage") {
          const { results } = await env.DB.prepare("SELECT status, finished_at FROM runs WHERE agent = ? AND subject = ? ORDER BY dispatched_at DESC LIMIT 5")
            .bind(it.agent, it.subject)
            .all<{ status: string; finished_at: string | null }>();
          debounced = isDebounced(results ?? [], now, it.agent === "reviewer" ? DEBOUNCE_MS : SECURITY_DEBOUNCE_MS);
        }
        if (debounced) o.skipped = "debounced";
        else {
          const r = await dispatchRun(
            env,
            {
              agent: it.agent,
              subject: it.subject,
              reservedCalls: spec.maxModelCalls,
              trigger: `webhook:${event}`,
              inputs: { run_id: makeRunId(it.agent, it.subject, now), issue: it.issue, pr: it.pr, phase: it.phase, dry_run: "false" },
            },
            ctx,
            now,
          );
          if (r.ok) o.run_id = r.runId;
          else {
            o.skipped = r.reason;
            if (r.reason === "dispatch_failed") failed = true;
          }
        }
      }
    }
    outcomes.push(o);
    void sendEvent(env, { name: "webhook.decision", event, ...o }, ctx);
  }
  if (failed) {
    // allow GitHub redelivery to retry
    await env.DB.prepare("DELETE FROM events WHERE delivery_id = ?").bind(delivery).run();
    return json({ outcomes }, 502);
  }
  return json({ outcomes }, 202);
}
