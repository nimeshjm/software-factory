// GET /dashboard: what happened, what is blocked, what happens next. Read-only.
// buildDashboard and dashboardHtml are pure (unit-tested); handleDashboard does the I/O.
import { agentSpec, AGENT_SPECS, dailyPaidCap, dailyQuota, maxOpenFactoryPrs, runPaidCap } from "./config";
import type { GhIssue, GhPR } from "./github";
import { listIssues, listMergeBlockers, listOpenPRs } from "./github";
import {
  BUG_TYPES,
  BUILDER_TYPES,
  candidateQueue,
  reviseCandidates,
  countFactoryPRs,
  failureBackoff,
  isDependabot,
  prReferences,
  STALE_GRACE_MIN,
  urgentBugs,
  type IssueInfo,
  type PRInfo,
  type RecentRun,
} from "./preflight";
import { computePaidBudget, computeQuota, isPaused, paidExhausted, utcMonth } from "./quota";
import type { Env, RunRow } from "./types";
import { IN_FLIGHT_STATUSES } from "./types";
import { json, startOfUtcDay } from "./util";
import {
  AUTO_REFRESH_SECONDS,
  DEFAULT_UI,
  STORAGE_PREFIX,
  issueUrl,
  prUrl,
  resolveUi,
  type DashboardUi,
} from "./dashboard-shared";
import { THEMES, dashboardHtmlLegacy } from "./dashboard-legacy";
import { NEXT_THEMES, dashboardHtmlNext } from "./dashboard-next";

export { AUTO_REFRESH_SECONDS, DEFAULT_UI, STORAGE_PREFIX, resolveUi, type DashboardUi } from "./dashboard-shared";
export { THEMES, dashboardHtmlLegacy } from "./dashboard-legacy";
export { NEXT_THEMES, dashboardHtmlNext } from "./dashboard-next";

export const DEFAULT_WINDOW_HOURS = 48;
export const MAX_WINDOW_HOURS = 168;
const TICK_MINUTES = 15;
const DEPENDABOT_REVIEW_MINUTES = 60;
const DAILY_HOUR_UTC = 6;

export interface DashboardInput {
  now: Date;
  windowHours: number;
  paused: boolean;
  lastTick: string | null;
  dailyQuota: number;
  maxOpenFactoryPrs: number;
  /** In-flight rows plus every row dispatched or finished inside the window (and today, for quota). */
  runs: RunRow[];
  /** null when GitHub could not be read; the ledger half of the page still renders. */
  openIssues: GhIssue[] | null;
  openPRs: GhPR[] | null;
  /** Issues and pull requests updated since the window start, any state. */
  updatedSince: GhIssue[] | null;
  targetRepo: string;
  githubError: string | null;
  /** Blockers parsed from each `merge:blocked` PR's factory comment, by PR number; null or missing when unreadable. */
  mergeBlockers?: Record<number, string[] | null>;
  /** Daily paid budget (CONTRACTS.md, Daily paid budget); missing = no paid key configured (cap 0). */
  paid?: { cap: number; runCap: number; blocked: boolean };
}

export type BlockKind =
  | "paused"
  | "overdue-run"
  | "stuck-in-progress"
  | "orphan-in-review"
  | "failed-run"
  | "failure-cap"
  | "plan-review"
  | "needs-human"
  | "merge-blocked"
  | "awaiting-risk"
  | "author-not-trusted"
  | "blocked-by"
  | "backoff"
  | "lit-pr"
  | "needs-human-pr"
  | "bugs-first"
  | "backpressure"
  | "quota"
  | "paid-blocked"
  | "paid-budget";

const BLOCK_ORDER: BlockKind[] = [
  "paused", "overdue-run", "stuck-in-progress", "orphan-in-review", "failed-run", "failure-cap", "plan-review", "needs-human",
  "merge-blocked", "awaiting-risk", "author-not-trusted", "blocked-by", "backoff", "lit-pr", "needs-human-pr", "bugs-first", "backpressure", "quota", "paid-blocked", "paid-budget",
];
/** Kinds a human has to act on; the rest clear on their own (a dependency closes, the day rolls over). */
const HUMAN_KINDS = new Set<BlockKind>([
  "paused", "stuck-in-progress", "orphan-in-review", "failed-run", "failure-cap", "plan-review", "needs-human", "merge-blocked", "awaiting-risk",
  "author-not-trusted", "lit-pr", "needs-human-pr", "backpressure", "paid-blocked",
]);

export interface Blocker {
  kind: BlockKind;
  /** `issue:N`, `pr:N`, `run:<id>` or `factory`. */
  subject: string;
  title: string;
  reason: string;
  /** Plain-English step for a human (set a label, approve a PR), or what it is waiting on. */
  todo: string;
  /** What clears it: a command when one exists, otherwise a sentence. */
  action: string;
  url: string | null;
  needs_human: boolean;
  /** Raw run error text for `failed-run`; null when the run reported none. */
  detail?: string | null;
}

export interface RunSummary {
  run_id: string;
  agent: string;
  subject: string;
  trigger: string | null;
  status: string;
  stop_reason: string | null;
  model_calls: number;
  dispatched_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_min: number | null;
  issue_number: number | null;
  pr_number: number | null;
  workflow_run_url: string | null;
  /** Estimated paid-key spend in USD (0 on the free key or when the run reported none). */
  paid_cost_usd: number;
}

export interface QueueItem {
  number: number;
  title: string;
  eligible: boolean;
  reason: string | null;
  labels: string[];
  url: string | null;
}

export interface DashboardDoc {
  generated_at: string;
  window_hours: number;
  window_start: string;
  target_repo: string;
  paused: boolean;
  today: { model_calls: number; reserved: number; quota: number; remaining: number; resets_at: string };
  in_flight: {
    run_id: string; agent: string; subject: string; status: string; dispatched_at: string; started_at: string | null;
    age_min: number; overdue: boolean; workflow_run_url: string | null;
  }[];
  blocked: Blocker[];
  next: {
    next_tick_at: string;
    queue: { agent: string; gate: string | null; items: QueueItem[] }[];
    scheduled: { agent: string; subject: string; at: string | null; note: string }[];
    last_tick: { at: string; decisions: unknown[] } | null;
  };
  happened: {
    runs: RunSummary[];
    counts: Record<string, Record<string, number>>;
    calls_by_day: { day: string; calls: number }[];
    target: {
      closed_issues: { number: number; title: string; closed_at: string; labels: string[]; url: string | null }[];
      merged_prs: { number: number; title: string; merged_at: string; author: string; url: string | null }[];
      closed_prs: { number: number; title: string; closed_at: string; author: string; url: string | null }[];
    };
  };
  /** Estimated paid-key spend (token usage x billing.prices_usd_per_m); the free key costs nothing. */
  costs: {
    today: {
      spent_usd: number; reserved_usd: number; cap_usd: number; remaining_usd: number; run_cap_usd: number;
      blocked: boolean; exhausted: boolean; resets_at: string;
    };
    window_usd: number;
    by_day: { day: string; usd: number }[];
    by_agent: { agent: string; usd: number; runs: number; paid_runs: number }[];
  };
  github_error: string | null;
}

/** USD to 4 decimals for display and JSON; costs are estimates, so more precision only adds noise. */
export const usd4 = (n: number): number => Math.round(n * 10_000) / 10_000;
const paidOf = (r: RunRow): number => (typeof r.paid_cost_usd === "number" && Number.isFinite(r.paid_cost_usd) ? r.paid_cost_usd : 0);

// ------------------------------------------------------------------ helpers

export function clampWindow(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_WINDOW_HOURS;
  return Math.min(MAX_WINDOW_HOURS, Math.max(1, Math.floor(n)));
}

export function nextTickAt(now: Date): Date {
  const ms = TICK_MINUTES * 60_000;
  return new Date(Math.floor(now.getTime() / ms) * ms + ms);
}

function nextDailyAt(now: Date, ranToday: boolean): Date {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), DAILY_HOUR_UTC));
  if (now.getUTCHours() === DAILY_HOUR_UTC && !ranToday) return nextTickAt(now);
  if (now.getTime() >= d.getTime() + 3600_000 || ranToday) d.setUTCDate(d.getUTCDate() + 1);
  else if (now.getTime() > d.getTime()) return nextTickAt(now);
  return d;
}

function minutesBetween(a: string | null, b: string | null): number | null {
  if (!a || !b) return null;
  const ms = Date.parse(b) - Date.parse(a);
  return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 60_000)) : null;
}

function toIssueInfo(i: GhIssue): IssueInfo {
  return { number: i.number, labels: i.labels, authorAssociation: i.authorAssociation, body: i.body };
}
function toPRInfo(p: GhPR): PRInfo {
  return { number: p.number, headRef: p.headRef, body: p.body, author: p.author, labels: p.labels };
}

function runIssue(r: RunRow): number | null {
  if (r.issue_number) return r.issue_number;
  const m = /^issue:(\d+)$/.exec(r.subject);
  return m ? Number(m[1]) : null;
}

/** Pure: why a run failed, plus what the tick will do about it (same backoff computation as the tick). */
function failedRunReason(r: RunRow, now: Date, recent: RecentRun[]): string {
  const why = (r.error || r.stop_reason || "").replace(/[.\s]+$/, "");
  let s = `Last run ${r.status} at ${r.finished_at ?? "?"}${why ? `: ${why}` : ""}.`;
  const subject = r.issue_number ? `issue:${r.issue_number}` : r.subject;
  const back = /^(issue|revise):/.test(subject) ? failureBackoff(subject, recent, now) : null;
  if (back?.startsWith("retry-after:")) s += ` Retries automatically after ${back.slice(back.indexOf("T") + 1, -1)} UTC.`;
  else if (back?.startsWith("failure-cap:")) s += ` Failure cap: no retry until ${back.slice("failure-cap:".length, -1).replace("T", " ")} UTC.`;
  else if (back === null && /^(issue|revise):/.test(subject)) s += " The backoff is over, so the next tick retries it.";
  else s += " Nothing will retry it.";
  return s;
}

/** One plain-English sentence per block kind: what a human does on GitHub, or what the item is waiting for. */
export function humanTodo(kind: BlockKind, subject: string): string {
  const m = /^(issue|pr|run):(.+)$/.exec(subject);
  const ref = m ? (m[1] === "run" ? "the run" : `${m[1] === "pr" ? "PR" : "issue"} #${m[2]}`) : "it";
  switch (kind) {
    case "paused": return "Resume the factory once whatever made you pause it is fixed.";
    case "overdue-run": return "Wait for the next tick to mark it lost. If the Actions run died, move its issue back to status:backlog.";
    case "stuck-in-progress": return `On ${ref}, swap the label status:in-progress for status:backlog so a tick picks it up again.`;
    case "orphan-in-review": return `Check whether the PR for ${ref} merged. If it did, label it status:done and close it. If not, relabel it status:backlog.`;
    case "failed-run": return "Read the run log. If the failure was transient, relabel the issue status:backlog to retry. If a guard stopped it, fix the cause first.";
    case "failure-cap": return `Read why the last runs on ${ref} failed and fix the cause, then re-run it by hand or wait for the cap to lift.`;
    case "plan-review": return `Read the plan comment on ${ref}. If it looks right, add the label plan:approved.`;
    case "needs-human": return `Do what the factory asked for in the comments on ${ref}, then remove the label needs-human.`;
    case "merge-blocked": return `Read why the merge is blocked on ${ref}. Fix it, or merge it yourself if the blockers are acceptable.`;
    case "awaiting-risk": return `Size ${ref}: add the label risk:low to let the builder go ahead, or risk:high to hold its plan for your review.`;
    case "author-not-trusted": return `Read ${ref}. If the factory should work on it, add the label factory:accepted.`;
    case "blocked-by": return "Nothing to do: it starts when the issues it depends on close.";
    case "backoff": return "Nothing to do: the tick retries it automatically after the backoff.";
    case "lit-pr": return `Review ${ref} and approve it on GitHub. The reviewer agent merges it once checks are green and threads are resolved.`;
    case "needs-human-pr": return `Take over ${ref}: fix it by hand (see the factory comments), then remove the label needs-human.`;
    case "bugs-first": return "Nothing to do unless the bug can wait: label it needs-human to let the builder move on.";
    case "backpressure": return "Merge or close one of the open factory PRs so the builder can start another.";
    case "quota": return "Nothing to do: the daily model-call quota resets at midnight UTC.";
    case "paid-blocked": return "Top up the Gemini prepay balance or raise the spend cap in AI Studio, then resume the factory to use the paid key again.";
    case "paid-budget": return "Nothing to do: the paid budget resets at midnight UTC and the free key's quota around 08:00 UTC.";
  }
}

/** Short hint for a held queue item, keyed by the tick's skip reason. */
export function queueHint(reason: string | null): string {
  if (!reason) return "";
  if (reason === "needs-human") return "you: act, then remove needs-human";
  if (reason === "awaiting-risk") return "you: add risk:low or risk:high";
  if (reason === "author-not-trusted") return "you: add factory:accepted";
  if (reason === "has-open-pr") return "waits: its PR is open";
  if (reason === "revise") return "";
  if (reason.startsWith("blocked-by:")) return "waits: dependency open";
  if (reason.startsWith("retry-after:")) return "waits: retry backoff";
  if (reason.startsWith("failure-cap:")) return "you: check the failed runs";
  return "";
}

// ------------------------------------------------------------------ pure build

export function buildDashboard(input: DashboardInput): DashboardDoc {
  const { now, targetRepo } = input;
  const nowMs = now.getTime();
  const windowStart = new Date(nowMs - input.windowHours * 3600_000).toISOString();
  const q = computeQuota(input.runs, now);
  const remaining = Math.max(0, input.dailyQuota - q.usedToday - q.reservedInFlight);
  const resets = new Date(startOfUtcDay(now));
  resets.setUTCDate(resets.getUTCDate() + 1);
  const edit = (n: number, args: string) => `gh issue edit ${n} -R ${targetRepo} ${args}`;

  // ---- in flight
  const inFlightRows = input.runs.filter((r) => (IN_FLIGHT_STATUSES as string[]).includes(r.status));
  const in_flight = inFlightRows
    .map((r) => {
      const deadline = Date.parse(r.dispatched_at) + (agentSpec(r.agent).timeoutMinutes + STALE_GRACE_MIN) * 60_000;
      return {
        run_id: r.id, agent: r.agent, subject: r.subject, status: r.status, dispatched_at: r.dispatched_at, started_at: r.started_at,
        age_min: minutesBetween(r.dispatched_at, now.toISOString()) ?? 0,
        overdue: deadline < nowMs,
        workflow_run_url: r.workflow_run_url,
      };
    })
    .sort((a, b) => a.dispatched_at.localeCompare(b.dispatched_at));
  const inFlightIssues = new Set(inFlightRows.map(runIssue).filter((n): n is number => n !== null));
  const inFlightAgents = new Set(inFlightRows.map((r) => r.agent));

  // ---- happened: runs in the window, newest first
  const windowRuns = input.runs
    .filter((r) => r.dispatched_at >= windowStart || (r.finished_at ?? "") >= windowStart || (IN_FLIGHT_STATUSES as string[]).includes(r.status))
    .sort((a, b) => b.dispatched_at.localeCompare(a.dispatched_at));
  const runs: RunSummary[] = windowRuns.map((r) => ({
    run_id: r.id, agent: r.agent, subject: r.subject, trigger: r.trigger, status: r.status, stop_reason: r.stop_reason,
    model_calls: r.model_calls, dispatched_at: r.dispatched_at, started_at: r.started_at, finished_at: r.finished_at,
    duration_min: minutesBetween(r.started_at ?? r.dispatched_at, r.finished_at),
    issue_number: r.issue_number, pr_number: r.pr_number, workflow_run_url: r.workflow_run_url,
    paid_cost_usd: usd4(paidOf(r)),
  }));
  const counts: Record<string, Record<string, number>> = {};
  for (const r of windowRuns) {
    const status = (IN_FLIGHT_STATUSES as string[]).includes(r.status) ? "in_flight" : r.status;
    counts[r.agent] ??= {};
    counts[r.agent][status] = (counts[r.agent][status] ?? 0) + 1;
  }
  const byDay = new Map<string, number>();
  for (let d = new Date(startOfUtcDay(new Date(windowStart))); d.getTime() <= nowMs; d.setUTCDate(d.getUTCDate() + 1)) byDay.set(d.toISOString().slice(0, 10), 0);
  for (const r of windowRuns) {
    if (!r.finished_at || r.finished_at < windowStart) continue;
    const day = r.finished_at.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + r.model_calls);
  }
  const calls_by_day = [...byDay.entries()].map(([day, calls]) => ({ day, calls }));

  // ---- costs: paid spend by finish day and by agent, and today's budget
  const usdByDay = new Map([...byDay.keys()].map((day) => [day, 0]));
  const byAgent = new Map<string, { usd: number; runs: number; paid_runs: number }>();
  for (const r of windowRuns) {
    const a = byAgent.get(r.agent) ?? { usd: 0, runs: 0, paid_runs: 0 };
    a.runs += 1;
    const cost = paidOf(r);
    if (cost > 0) a.paid_runs += 1;
    a.usd += cost;
    byAgent.set(r.agent, a);
    if (r.finished_at && r.finished_at >= windowStart) usdByDay.set(r.finished_at.slice(0, 10), (usdByDay.get(r.finished_at.slice(0, 10)) ?? 0) + cost);
  }
  const paidCfg = input.paid ?? { cap: 0, runCap: 0, blocked: false };
  const budget = computePaidBudget(input.runs, now, paidCfg.cap, paidCfg.runCap, paidCfg.blocked);
  const costs: DashboardDoc["costs"] = {
    today: {
      spent_usd: usd4(budget.spentToday), reserved_usd: usd4(budget.reservedInFlight), cap_usd: paidCfg.cap,
      remaining_usd: usd4(Math.max(0, paidCfg.cap - budget.spentToday - budget.reservedInFlight)), run_cap_usd: paidCfg.runCap,
      blocked: paidCfg.blocked, exhausted: paidExhausted(budget),
      resets_at: new Date(Date.parse(startOfUtcDay(now)) + 86_400_000).toISOString(),
    },
    window_usd: usd4([...usdByDay.values()].reduce((a, b) => a + b, 0)),
    by_day: [...usdByDay.entries()].map(([day, usd]) => ({ day, usd: usd4(usd) })),
    by_agent: [...byAgent.entries()].map(([agent, v]) => ({ agent, usd: usd4(v.usd), runs: v.runs, paid_runs: v.paid_runs }))
      .sort((a, b) => b.usd - a.usd || a.agent.localeCompare(b.agent)),
  };

  // ---- happened: target movement
  const since = input.updatedSince ?? [];
  const target = {
    closed_issues: since
      .filter((i) => !i.isPullRequest && i.state === "closed" && (i.closedAt ?? "") >= windowStart)
      .map((i) => ({ number: i.number, title: i.title, closed_at: i.closedAt!, labels: i.labels, url: i.htmlUrl }))
      .sort((a, b) => b.closed_at.localeCompare(a.closed_at)),
    merged_prs: since
      .filter((i) => i.isPullRequest && (i.mergedAt ?? "") >= windowStart)
      .map((i) => ({ number: i.number, title: i.title, merged_at: i.mergedAt!, author: i.author, url: i.htmlUrl }))
      .sort((a, b) => b.merged_at.localeCompare(a.merged_at)),
    closed_prs: since
      .filter((i) => i.isPullRequest && !i.mergedAt && i.state === "closed" && (i.closedAt ?? "") >= windowStart)
      .map((i) => ({ number: i.number, title: i.title, closed_at: i.closedAt!, author: i.author, url: i.htmlUrl }))
      .sort((a, b) => b.closed_at.localeCompare(a.closed_at)),
  };

  // ---- blocked
  const blocked: Blocker[] = [];
  const add = (b: Omit<Blocker, "needs_human" | "todo">) =>
    blocked.push({ ...b, todo: humanTodo(b.kind, b.subject), needs_human: HUMAN_KINDS.has(b.kind) });
  if (input.paused) {
    add({
      kind: "paused", subject: "factory", title: "Control plane paused", reason: "The kill switch is on: ticks dispatch nothing and webhooks are skipped.",
      action: 'curl -X POST -H "Authorization: Bearer $FACTORY_TOKEN" $FACTORY_URL/admin/resume', url: null,
    });
  }
  for (const f of in_flight) {
    if (f.overdue) {
      add({
        kind: "overdue-run", subject: `run:${f.run_id}`, title: `${f.agent} ${f.subject}`,
        reason: `Dispatched ${f.age_min} min ago and past its timeout without reporting; the next tick marks it lost and frees the agent.`,
        action: "Open the Actions run; if it is still running, wait; if it died, the issue it carried needs status:backlog again.",
        url: f.workflow_run_url,
      });
    }
  }
  // Latest run per subject that ended badly, unless a later run on the same subject succeeded.
  // A manual rerun carries subject manual:<id>, so runs are keyed by their issue or PR number when they have one.
  const subjectKey = (r: RunRow) => (r.issue_number ? `issue:${r.issue_number}` : r.pr_number ? `pr:${r.pr_number}` : r.subject);
  const latestBySubject = new Map<string, RunRow>();
  for (const r of windowRuns) if (!latestBySubject.has(subjectKey(r))) latestBySubject.set(subjectKey(r), r);
  for (const r of latestBySubject.values()) {
    if (r.status === "failed" || r.status === "timeout" || r.status === "lost") {
      add({
        kind: "failed-run", subject: `run:${r.id}`, title: `${r.agent} ${r.subject}`,
        reason: failedRunReason(r, input.now, input.runs.map((x) => ({ agent: x.agent, subject: x.subject, dispatchedAt: x.dispatched_at, status: x.status }))),
        action: r.error && /blocked:|protected/.test(r.error)
          ? "A guard blocked it, so a retry fails the same way: fix the cause first (the blocked path or command is named above)."
          : "Read the run log. A guard stop (quota, latency, context, turns) repeats on retry; a transient GitHub error is cleared by relabelling the issue status:backlog.",
        url: r.workflow_run_url,
        detail: r.error ?? null,
      });
    }
  }

  const issues = input.openIssues ?? [];
  const prs = input.openPRs ?? [];
  const openPRInfos = prs.map(toPRInfo);
  const stateLike = {
    openIssues: issues.map(toIssueInfo),
    openPRs: openPRInfos,
    recentRuns: input.runs.map((r) => ({ agent: r.agent, subject: r.subject, dispatchedAt: r.dispatched_at, status: r.status, stopReason: r.stop_reason ?? null })),
  };
  const titleOf = new Map(issues.map((i) => [i.number, i.title]));

  for (const i of issues) {
    const url = i.htmlUrl ?? issueUrl(targetRepo, i.number);
    if (i.labels.includes("status:in-progress") && !inFlightIssues.has(i.number)) {
      add({
        kind: "stuck-in-progress", subject: `issue:${i.number}`, title: i.title,
        reason: "Labelled status:in-progress but no run is in flight on it: a run died without resetting the label, so no tick will pick it up.",
        action: edit(i.number, "--remove-label status:in-progress --add-label status:backlog"), url,
      });
    }
    if (i.labels.includes("status:in-review") && !openPRInfos.some((p) => prReferences(p, i.number))) {
      add({
        kind: "orphan-in-review", subject: `issue:${i.number}`, title: i.title,
        reason: "Labelled status:in-review but no open PR references it: the PR merged or closed without the issue moving on.",
        action: `If the PR merged: ${edit(i.number, "--remove-label status:in-review --add-label status:done")} && gh issue close ${i.number} -R ${targetRepo}; otherwise relabel status:backlog.`,
        url,
      });
    }
    if (i.labels.includes("status:plan-review") && !i.labels.includes("plan:approved")) {
      add({
        kind: "plan-review", subject: `issue:${i.number}`, title: i.title,
        reason: "The plan is posted and held for a human (risk:high or the plan asked for one). Nothing happens until it is approved.",
        action: `Read the plan comment, then ${edit(i.number, "--add-label plan:approved")}`, url,
      });
    }
  }

  const queues = [
    { agent: "builder", items: candidateQueue(stateLike, BUILDER_TYPES, now) },
    { agent: "bug-fixer", items: candidateQueue(stateLike, BUG_TYPES, now) },
  ];
  for (const { agent, items } of queues) {
    for (const c of items) {
      const n = c.issue.number;
      const url = issues.find((i) => i.number === n)?.htmlUrl ?? issueUrl(targetRepo, n);
      const title = titleOf.get(n) ?? "";
      if (c.reason === "needs-human") {
        add({
          kind: "needs-human", subject: `issue:${n}`, title,
          reason: `In the ${agent} backlog but parked on a human (needs-human).`,
          action: `When you have acted: ${edit(n, "--remove-label needs-human")}`, url,
        });
      } else if (c.reason === "awaiting-risk") {
        add({
          kind: "awaiting-risk", subject: `issue:${n}`, title,
          reason: "Backlog feature with neither risk:low nor risk:high, so the builder cannot size the gate.",
          action: edit(n, "--add-label risk:low") + "   # or risk:high to hold the plan for review", url,
        });
      } else if (c.reason === "author-not-trusted") {
        add({
          kind: "author-not-trusted", subject: `issue:${n}`, title,
          reason: "Filed by someone who is not a collaborator and not accepted by a human.",
          action: edit(n, "--add-label factory:accepted"), url,
        });
      } else if (c.reason?.startsWith("blocked-by:")) {
        add({
          kind: "blocked-by", subject: `issue:${n}`, title,
          reason: `Depends on open ${c.reason.slice("blocked-by:".length)}; it is picked up once they close.`,
          action: "Nothing to do unless the dependency is wrong in the issue body.", url,
        });
      } else if (c.reason?.startsWith("retry-after:")) {
        add({
          kind: "backoff", subject: `issue:${n}`, title,
          reason: `Its last run failed; the tick retries at ${c.reason.slice("retry-after:".length)} (1 failure: 60 min, 2: 180 min, 3 in 24 h: stop).`,
          action: `Nothing to do unless you want it now: gh workflow run ${agent}.yml -f issue=${n}`, url,
        });
      } else if (c.reason?.startsWith("failure-cap:")) {
        add({
          kind: "failure-cap", subject: `issue:${n}`, title,
          reason: `3 or more failed runs in 24 h; the tick stops retrying until ${c.reason.slice("failure-cap:".length)}. Read the failed runs' stop_reason first.`,
          action: `gh workflow run ${agent}.yml -f issue=${n}`, url,
        });
      }
    }
  }

  for (const p of prs) {
    const url = p.htmlUrl ?? prUrl(targetRepo, p.number);
    if (p.labels.includes("needs-human")) {
      add({
        kind: "needs-human-pr", subject: `pr:${p.number}`, title: p.title,
        reason: p.labels.includes("revise:attempted")
          ? "revise attempted; a human must take over (see the factory comments on the PR)"
          : "The pipeline could not handle this PR (needs-human); the reviewer skips it.",
        action: `Fix it by hand, then gh pr edit ${p.number} -R ${targetRepo} --remove-label needs-human`, url,
      });
    } else if (p.labels.includes("merge:blocked")) {
      const bl = input.mergeBlockers?.[p.number];
      add({
        kind: "merge-blocked", subject: `pr:${p.number}`, title: p.title,
        reason: bl?.length
          ? `Approved, but the merge rules block it: ${bl.join("; ")}.`
          : "Approved, but the merge rules block it (see the factory comment on the PR).",
        action: `Fix the blockers, or merge it by hand if you accept them: gh pr merge ${p.number} -R ${targetRepo} --squash`, url,
      });
    } else if (p.labels.includes("autonomy:lit")) {
      add({
        kind: "lit-pr", subject: `pr:${p.number}`, title: p.title,
        reason: `Lit PR (${isDependabot(p.author) ? "dependency bump outside dark paths" : p.headRef}): it merges only after a human APPROVED review with green checks and no open threads.`,
        action: `Review it; an approval dispatches the reviewer, which merges it: gh pr review ${p.number} -R ${targetRepo} --approve`, url,
      });
    }
  }

  const builderQueue = queues[0].items;
  const urgent = urgentBugs(stateLike.openIssues);
  const factoryPrs = countFactoryPRs(openPRInfos);
  let builderGate: string | null = null;
  if (builderQueue.length) {
    if (factoryPrs >= input.maxOpenFactoryPrs) {
      builderGate = "backpressure";
      add({
        kind: "backpressure", subject: "factory", title: `${factoryPrs} open factory PRs (max ${input.maxOpenFactoryPrs})`,
        reason: "The builder stops while this many feat/ or fix/ PRs are open.",
        action: "Merge or close a factory PR.", url: `https://github.com/${targetRepo}/pulls`,
      });
    } else if (urgent.length) {
      builderGate = "bugs_first";
      add({
        kind: "bugs-first", subject: "factory", title: `Open bug${urgent.length > 1 ? "s" : ""} ${urgent.map((b) => `#${b.number}`).join(", ")} at priority 1 or 2`,
        reason: "Bugs first: the builder waits for features while an urgent bug is open and not parked on a human.",
        action: "Let the bug-fixer finish, or park the bug: " + edit(urgent[0].number, "--add-label needs-human"),
        url: issueUrl(targetRepo, urgent[0].number),
      });
    } else if (inFlightAgents.has("builder")) builderGate = "in_flight";
    else if (builderQueue.some((c) => c.reason === null) && remaining < AGENT_SPECS.builder.maxModelCalls) builderGate = "quota";
  }
  let bugGate: string | null = null;
  const bugQueue = queues[1].items;
  if (bugQueue.length) {
    if (inFlightAgents.has("bug-fixer")) bugGate = "in_flight";
    else if (bugQueue.some((c) => c.reason === null) && remaining < AGENT_SPECS["bug-fixer"].maxModelCalls) bugGate = "quota";
  }
  if (builderGate === "quota" || bugGate === "quota") {
    add({
      kind: "quota", subject: "factory", title: `${remaining} model calls left today`,
      reason: `An eligible issue is waiting but a builder or bug-fixer run reserves ${AGENT_SPECS.builder.maxModelCalls} calls.`,
      action: `Nothing to do: the daily ceiling (${input.dailyQuota}) resets at ${resets.toISOString()}.`, url: null,
    });
  }
  if (costs.today.blocked) {
    add({
      kind: "paid-blocked", subject: "factory", title: `Paid Gemini key blocked for ${utcMonth(now)}`,
      reason: "A run hit the prepay balance or the monthly spend cap. Runs continue on the free key only, and stop when its daily quota runs out.",
      action: 'curl -X POST -H "Authorization: Bearer $FACTORY_TOKEN" $FACTORY_URL/admin/resume', url: null,
    });
  }
  if (costs.today.exhausted) {
    add({
      kind: "paid-budget", subject: "factory", title: `Paid budget spent: $${costs.today.spent_usd.toFixed(2)} of $${paidCfg.cap.toFixed(2)} today`,
      reason: "A run stopped because the free quota was used up and today's paid budget was too. Ticks dispatch nothing until one of them resets.",
      action: `Nothing to do: the paid budget resets at ${costs.today.resets_at}; raise billing.daily_paid_cap_usd to spend more.`, url: null,
    });
  }
  blocked.sort((a, b) => BLOCK_ORDER.indexOf(a.kind) - BLOCK_ORDER.indexOf(b.kind) || a.subject.localeCompare(b.subject));

  // ---- next
  const reviseItems = (agent: string): QueueItem[] =>
    reviseCandidates(stateLike, now)
      .filter((r) => r.agent === agent)
      .map((r) => {
        const p = prs.find((x) => x.number === r.pr);
        return {
          number: r.pr, title: p?.title ?? "", eligible: r.reason === null, reason: r.reason ?? "revise",
          labels: p?.labels ?? [], url: p?.htmlUrl ?? prUrl(targetRepo, r.pr),
        };
      });
  const toItem = (c: { issue: IssueInfo; reason: string | null }): QueueItem => ({
    number: c.issue.number, title: titleOf.get(c.issue.number) ?? "", eligible: c.reason === null, reason: c.reason, labels: c.issue.labels,
    url: issues.find((i) => i.number === c.issue.number)?.htmlUrl ?? issueUrl(targetRepo, c.issue.number),
  });
  const unlabeled = issues.filter((i) => i.labels.length === 0).sort((a, b) => a.number - b.number);
  const queue = [
    { agent: "triage", gate: inFlightAgents.has("triage") ? "in_flight" : null, items: unlabeled.map((i) => ({ number: i.number, title: i.title, eligible: true, reason: null, labels: [], url: i.htmlUrl })) },
    { agent: "bug-fixer", gate: bugGate, items: [...reviseItems("bug-fixer"), ...bugQueue.map(toItem)] },
    { agent: "builder", gate: builderGate, items: [...reviseItems("builder"), ...builderQueue.map(toItem)] },
  ];

  const scheduled: DashboardDoc["next"]["scheduled"] = [];
  const dependabot = prs.filter((p) => isDependabot(p.author) && !p.labels.includes("needs-human")).map((p) => p.number);
  if (dependabot.length) {
    const last = input.runs
      .filter((r) => r.agent === "reviewer" && r.subject === "dependabot")
      .map((r) => Date.parse(r.dispatched_at))
      .reduce((a, b) => Math.max(a, b), 0);
    const earliest = Math.max(nextTickAt(now).getTime(), last + (DEPENDABOT_REVIEW_MINUTES - 10) * 60_000);
    scheduled.push({
      agent: "reviewer", subject: "dependabot", at: nextTickAt(new Date(earliest - 1)).toISOString(),
      note: `hourly review of the oldest open Dependabot PR, #${Math.min(...dependabot)} (${dependabot.length} open)`,
    });
  }
  const today = now.toISOString().slice(0, 10);
  for (const agent of ["incident-responder", "security-triage"]) {
    const ranToday = input.runs.some((r) => r.agent === agent && r.subject === today);
    scheduled.push({ agent, subject: "daily", at: nextDailyAt(now, ranToday).toISOString(), note: `once a day in the 06:00 UTC tick${ranToday ? " (ran today)" : ""}` });
  }
  scheduled.push({ agent: "verifier", subject: "deploy", at: null, note: "after each successful Deploy of the default branch" });
  scheduled.push({ agent: "reviewer", subject: "pr", at: null, note: "on every factory PR push, check suite and human approval" });

  let last_tick: DashboardDoc["next"]["last_tick"] = null;
  try {
    last_tick = input.lastTick ? JSON.parse(input.lastTick) : null;
  } catch {
    last_tick = null;
  }

  return {
    generated_at: now.toISOString(),
    window_hours: input.windowHours,
    window_start: windowStart,
    target_repo: targetRepo,
    paused: input.paused,
    today: { model_calls: q.usedToday, reserved: q.reservedInFlight, quota: input.dailyQuota, remaining, resets_at: resets.toISOString() },
    in_flight,
    blocked,
    next: { next_tick_at: nextTickAt(now).toISOString(), queue, scheduled, last_tick },
    happened: { runs, counts, calls_by_day, target },
    costs,
    github_error: input.githubError,
  };
}

// ------------------------------------------------------------------ HTML

export function dashboardHtml(d: DashboardDoc, ui: DashboardUi = DEFAULT_UI): string {
  return ui === "old" ? dashboardHtmlLegacy(d) : dashboardHtmlNext(d);
}

// ------------------------------------------------------------------ I/O

export async function handleDashboard(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const now = new Date();
  const windowHours = clampWindow(url.searchParams.get("hours"));
  const ui = resolveUi(url.searchParams.get("ui"));
  const since = new Date(now.getTime() - windowHours * 3600_000).toISOString();
  const floor = since < startOfUtcDay(now) ? since : startOfUtcDay(now);
  const [runs, lt, paused, paidBlocked] = await Promise.all([
    env.DB.prepare("SELECT * FROM runs WHERE status IN ('dispatched','started') OR dispatched_at >= ? OR finished_at >= ? ORDER BY dispatched_at DESC LIMIT 500")
      .bind(floor, floor)
      .all<RunRow>(),
    env.DB.prepare("SELECT value FROM config WHERE key = 'last_tick'").first<{ value: string }>(),
    isPaused(env),
    env.DB.prepare("SELECT value FROM config WHERE key = 'paid_blocked_month'").first<{ value: string }>(),
  ]);
  let openIssues: GhIssue[] | null = null;
  let openPRs: GhPR[] | null = null;
  let updatedSince: GhIssue[] | null = null;
  let githubError: string | null = null;
  try {
    // 3 list calls (up to 3 pages each) plus cached token: well inside the 50-subrequest budget.
    [openPRs, openIssues, updatedSince] = await Promise.all([
      listOpenPRs(env, env.TARGET_REPO),
      listIssues(env, env.TARGET_REPO, { state: "open" }),
      listIssues(env, env.TARGET_REPO, { state: "closed", since, includePRs: true }),
    ]);
  } catch (e) {
    githubError = e instanceof Error ? e.message : String(e);
  }
  let mergeBlockers: Record<number, string[] | null> = {};
  if (openPRs) {
    // One comments call per merge:blocked PR (rare); a failed read falls back to the generic reason.
    const labelled = openPRs.filter((p) => p.labels.includes("merge:blocked") && !p.labels.includes("needs-human"));
    const got = await Promise.all(labelled.map((p) => listMergeBlockers(env, env.TARGET_REPO, p.number).catch(() => null)));
    mergeBlockers = Object.fromEntries(labelled.map((p, i) => [p.number, got[i]]));
  }
  const doc = buildDashboard({
    now, windowHours, paused, lastTick: lt?.value ?? null, dailyQuota: dailyQuota(env), maxOpenFactoryPrs: maxOpenFactoryPrs(env),
    runs: runs.results ?? [], openIssues, openPRs, updatedSince, targetRepo: env.TARGET_REPO, githubError, mergeBlockers,
    paid: { cap: dailyPaidCap(env), runCap: runPaidCap(env), blocked: paidBlocked?.value === utcMonth(now) },
  });
  const wantsHtml = url.searchParams.get("format") !== "json" && (req.headers.get("accept") ?? "").includes("text/html");
  const headers = { "cache-control": "public, max-age=60" };
  if (wantsHtml) return new Response(dashboardHtml(doc, ui), { headers: { ...headers, "content-type": "text/html; charset=utf-8" } });
  const res = json(doc);
  res.headers.set("cache-control", headers["cache-control"]);
  return res;
}
