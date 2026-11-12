// GET /dashboard: what happened, what is blocked, what happens next. Read-only.
// buildDashboard and dashboardHtml are pure (unit-tested); handleDashboard does the I/O.
import { agentSpec, AGENT_SPECS, dailyQuota, maxOpenFactoryPrs } from "./config";
import type { GhIssue, GhPR } from "./github";
import { listIssues, listOpenPRs } from "./github";
import {
  BUG_TYPES,
  BUILDER_TYPES,
  candidateQueue,
  countFactoryPRs,
  isDependabot,
  prReferences,
  STALE_GRACE_MIN,
  urgentBugs,
  type IssueInfo,
  type PRInfo,
} from "./preflight";
import { computeQuota, isPaused } from "./quota";
import type { Env, RunRow } from "./types";
import { IN_FLIGHT_STATUSES } from "./types";
import { json, startOfUtcDay } from "./util";

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
}

export type BlockKind =
  | "paused"
  | "overdue-run"
  | "stuck-in-progress"
  | "orphan-in-review"
  | "failed-run"
  | "plan-review"
  | "needs-human"
  | "awaiting-risk"
  | "author-not-trusted"
  | "blocked-by"
  | "lit-pr"
  | "needs-human-pr"
  | "bugs-first"
  | "backpressure"
  | "quota";

const BLOCK_ORDER: BlockKind[] = [
  "paused", "overdue-run", "stuck-in-progress", "orphan-in-review", "failed-run", "plan-review", "needs-human",
  "awaiting-risk", "author-not-trusted", "blocked-by", "lit-pr", "needs-human-pr", "bugs-first", "backpressure", "quota",
];
/** Kinds a human has to act on; the rest clear on their own (a dependency closes, the day rolls over). */
const HUMAN_KINDS = new Set<BlockKind>([
  "paused", "stuck-in-progress", "orphan-in-review", "failed-run", "plan-review", "needs-human", "awaiting-risk",
  "author-not-trusted", "lit-pr", "needs-human-pr", "backpressure",
]);

export interface Blocker {
  kind: BlockKind;
  /** `issue:N`, `pr:N`, `run:<id>` or `factory`. */
  subject: string;
  title: string;
  reason: string;
  /** What clears it: a command when one exists, otherwise a sentence. */
  action: string;
  url: string | null;
  needs_human: boolean;
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
  github_error: string | null;
}

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

function issueUrl(repo: string, n: number): string {
  return `https://github.com/${repo}/issues/${n}`;
}
function prUrl(repo: string, n: number): string {
  return `https://github.com/${repo}/pull/${n}`;
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
  const add = (b: Omit<Blocker, "needs_human">) => blocked.push({ ...b, needs_human: HUMAN_KINDS.has(b.kind) });
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
        reason: `Last run on this subject ended ${r.status}${r.stop_reason ? ` (${r.stop_reason})` : ""} at ${r.finished_at ?? "?"}; nothing has retried it.`,
        action: "Read the run log. A guard stop (quota, latency, context, turns) repeats on retry; a transient GitHub error is cleared by relabelling the issue status:backlog.",
        url: r.workflow_run_url,
      });
    }
  }

  const issues = input.openIssues ?? [];
  const prs = input.openPRs ?? [];
  const openPRInfos = prs.map(toPRInfo);
  const stateLike = { openIssues: issues.map(toIssueInfo), openPRs: openPRInfos };
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
    { agent: "builder", items: candidateQueue(stateLike, BUILDER_TYPES) },
    { agent: "bug-fixer", items: candidateQueue(stateLike, BUG_TYPES) },
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
      }
    }
  }

  for (const p of prs) {
    const url = p.htmlUrl ?? prUrl(targetRepo, p.number);
    if (p.labels.includes("needs-human")) {
      add({
        kind: "needs-human-pr", subject: `pr:${p.number}`, title: p.title,
        reason: "The pipeline could not handle this PR (needs-human); the reviewer skips it.",
        action: `Fix it by hand, then gh pr edit ${p.number} -R ${targetRepo} --remove-label needs-human`, url,
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
  blocked.sort((a, b) => BLOCK_ORDER.indexOf(a.kind) - BLOCK_ORDER.indexOf(b.kind) || a.subject.localeCompare(b.subject));

  // ---- next
  const toItem = (c: { issue: IssueInfo; reason: string | null }): QueueItem => ({
    number: c.issue.number, title: titleOf.get(c.issue.number) ?? "", eligible: c.reason === null, reason: c.reason, labels: c.issue.labels,
    url: issues.find((i) => i.number === c.issue.number)?.htmlUrl ?? issueUrl(targetRepo, c.issue.number),
  });
  const unlabeled = issues.filter((i) => i.labels.length === 0).sort((a, b) => a.number - b.number);
  const queue = [
    { agent: "triage", gate: inFlightAgents.has("triage") ? "in_flight" : null, items: unlabeled.map((i) => ({ number: i.number, title: i.title, eligible: true, reason: null, labels: [], url: i.htmlUrl })) },
    { agent: "bug-fixer", gate: bugGate, items: bugQueue.map(toItem) },
    { agent: "builder", gate: builderGate, items: builderQueue.map(toItem) },
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
    github_error: input.githubError,
  };
}

// ------------------------------------------------------------------ HTML

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

function ago(iso: string | null, now: Date): string {
  if (!iso) return "";
  const min = Math.round((now.getTime() - Date.parse(iso)) / 60_000);
  if (!Number.isFinite(min)) return "";
  if (min < -1) return `in ${fmtMin(-min)}`;
  if (min < 1) return "now";
  return `${fmtMin(min)} ago`;
}
function fmtMin(min: number): string {
  if (min < 60) return `${min} min`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} h ${min % 60} min`;
  return `${Math.floor(min / 1440)} d`;
}
function clock(iso: string | null): string {
  return iso ? iso.slice(5, 16).replace("T", " ") : "";
}
function link(url: string | null, text: string): string {
  return url ? `<a href="${esc(url)}">${esc(text)}</a>` : esc(text);
}
function pill(status: string): string {
  const cls = ["success"].includes(status) ? "good" : ["failed", "timeout", "lost"].includes(status) ? "bad" : ["dispatched", "started", "in_flight"].includes(status) ? "warn" : "mute";
  const icon = cls === "good" ? "&#10003;" : cls === "bad" ? "&#10007;" : cls === "warn" ? "&#8230;" : "&#8211;";
  return `<span class="pill ${cls}">${icon} ${esc(status)}</span>`;
}
function subjectLink(repo: string, subject: string, issue: number | null, pr: number | null): string {
  const m = /^(issue|pr):(\d+)$/.exec(subject);
  if (m) return link(m[1] === "pr" ? prUrl(repo, Number(m[2])) : issueUrl(repo, Number(m[2])), subject);
  const extra = pr ? ` ${link(prUrl(repo, pr), `pr:${pr}`)}` : issue ? ` ${link(issueUrl(repo, issue), `issue:${issue}`)}` : "";
  return esc(subject) + extra;
}
const table = (head: string[], rows: string[][]) =>
  rows.length
    ? `<table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    : `<p class="empty">none</p>`;

export function dashboardHtml(d: DashboardDoc): string {
  const now = new Date(d.generated_at);
  const repo = d.target_repo;
  const human = d.blocked.filter((b) => b.needs_human);
  const failed = d.happened.runs.filter((r) => ["failed", "timeout", "lost"].includes(r.status)).length;
  const used = d.today.model_calls + d.today.reserved;
  const pct = Math.min(100, Math.round((used / Math.max(1, d.today.quota)) * 100));
  const maxCalls = Math.max(1, ...d.happened.calls_by_day.map((x) => x.calls));
  const windowLinks = [24, 48, 168].map((h) => (h === d.window_hours ? `<b>${h} h</b>` : `<a href="?hours=${h}">${h} h</a>`)).join(" · ");

  const tiles = [
    ["Factory", d.paused ? `<span class="pill bad">&#10007; paused</span>` : `<span class="pill good">&#10003; running</span>`, `next tick ${ago(d.next.next_tick_at, now)}`],
    ["Needs you", String(human.length), human.length ? `${d.blocked.length} blocked in all` : "nothing waiting on a human"],
    ["In flight", String(d.in_flight.length), d.in_flight.some((f) => f.overdue) ? "one or more overdue" : d.in_flight.map((f) => f.agent).join(", ") || "idle"],
    [`Runs, last ${d.window_hours} h`, String(d.happened.runs.length), failed ? `${failed} failed` : "no failures"],
    ["Model calls today", `${used} <span class="dim">/ ${d.today.quota}</span>`, `<div class="meter"><div style="width:${pct}%"></div></div>${d.today.model_calls} used, ${d.today.reserved} reserved, ${d.today.remaining} left`],
    ["Shipped", String(d.happened.target.merged_prs.length + d.happened.target.closed_issues.length), `${d.happened.target.merged_prs.length} PRs merged, ${d.happened.target.closed_issues.length} issues closed`],
  ]
    .map(([l, v, s]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`)
    .join("");

  // One card per kind; the items inside share a cause, so the reason reads once per group when it repeats.
  const groups: { kind: string; items: Blocker[] }[] = [];
  for (const b of d.blocked) {
    const g = groups[groups.length - 1];
    if (g && g.kind === b.kind) g.items.push(b);
    else groups.push({ kind: b.kind, items: [b] });
  }
  const blockedHtml = groups.length
    ? groups
        .map(({ kind, items }) => {
          const you = items[0].needs_human;
          const shared = items.length > 1 && items.every((b) => b.reason === items[0].reason);
          const rows = items
            .map(
              (b) =>
                `<div class="item"><div>${link(b.url, b.subject)} <span class="dim">${esc(b.title)}</span></div>${shared ? "" : `<div class="dim">${esc(b.reason)}</div>`}<code class="act">${esc(b.action)}</code></div>`,
            )
            .join("");
          return `<div class="blk"><h3><span class="pill ${you ? "bad" : "mute"}">${you ? "&#9679; you" : "&#9675; waits"}</span> ${esc(kind)} <span class="dim">(${items.length})</span></h3>${shared ? `<p class="dim">${esc(items[0].reason)}</p>` : ""}${rows}</div>`;
        })
        .join("")
    : `<p class="empty">nothing is blocked</p>`;

  const flightRows = d.in_flight.map((f) => [
    esc(f.agent),
    subjectLink(repo, f.subject, null, null),
    pill(f.status) + (f.overdue ? ' <span class="pill bad">&#9888; overdue</span>' : ""),
    `${clock(f.dispatched_at)} <span class="dim">(${ago(f.dispatched_at, now)})</span>`,
    link(f.workflow_run_url, f.workflow_run_url ? "run" : ""),
  ]);

  const queueHtml = d.next.queue
    .map(({ agent, gate, items }) => {
      const gateText = gate ? `<span class="pill ${gate === "in_flight" ? "warn" : "mute"}">${esc(gate)}</span>` : "";
      const first = items.find((i) => i.eligible);
      const lead = !items.length
        ? `<p class="empty">nothing in its backlog</p>`
        : gate === "in_flight"
          ? `<p>busy now; ${first ? `then #${first.number}` : "nothing eligible after that"}</p>`
          : gate
            ? `<p>held (${esc(gate)}); first eligible would be ${first ? `#${first.number}` : "none"}</p>`
            : first
              ? `<p>next tick dispatches <b>${link(first.url, `#${first.number}`)}</b> ${esc(first.title)}</p>`
              : `<p>every candidate is blocked (see above)</p>`;
      const list = items.length
        ? `<ol>${items.map((i) => `<li class="${i.eligible ? "ok" : "held"}">${link(i.url, `#${i.number}`)} ${esc(i.title)} ${i.eligible ? "" : `<span class="pill mute">${esc(i.reason ?? "")}</span>`}</li>`).join("")}</ol>`
        : "";
      return `<div class="q"><h3>${esc(agent)} ${gateText}</h3>${lead}${list}</div>`;
    })
    .join("");

  const schedRows = d.next.scheduled.map((s) => [esc(s.agent), s.at ? `${clock(s.at)} <span class="dim">(${ago(s.at, now)})</span>` : "event-driven", esc(s.note)]);
  const decisions = (d.next.last_tick?.decisions ?? []).map((x) => `<li><code>${esc(JSON.stringify(x))}</code></li>`).join("");

  const t = d.happened.target;
  const movement = [
    ...t.merged_prs.map((p) => [clock(p.merged_at), '<span class="pill good">&#10003; merged</span>', link(p.url, `PR #${p.number}`), esc(p.title), esc(p.author)]),
    ...t.closed_issues.map((i) => [clock(i.closed_at), '<span class="pill good">&#10003; closed</span>', link(i.url, `#${i.number}`), esc(i.title), esc(i.labels.filter((l) => l.startsWith("status:")).join(" "))]),
    ...t.closed_prs.map((p) => [clock(p.closed_at), '<span class="pill mute">&#8211; closed unmerged</span>', link(p.url, `PR #${p.number}`), esc(p.title), esc(p.author)]),
  ].sort((a, b) => b[0].localeCompare(a[0]));

  const runRows = d.happened.runs.map((r) => [
    `${clock(r.dispatched_at)} <span class="dim">(${ago(r.dispatched_at, now)})</span>`,
    esc(r.agent),
    subjectLink(repo, r.subject, r.issue_number, r.pr_number),
    esc(r.trigger ?? ""),
    pill(r.status) + (r.stop_reason && r.stop_reason !== "end_turn" ? ` <span class="dim">${esc(r.stop_reason)}</span>` : ""),
    r.duration_min === null ? "" : `${r.duration_min} min`,
    String(r.model_calls),
    link(r.workflow_run_url, r.workflow_run_url ? "log" : ""),
  ]);
  const agents = Object.keys(d.happened.counts).sort();
  const statuses = ["success", "failed", "timeout", "lost", "denied", "in_flight"];
  const countRows = agents.map((a) => [esc(a), ...statuses.map((s) => String(d.happened.counts[a][s] ?? ""))]);
  const bars = d.happened.calls_by_day
    .map((x) => `<div class="bar" title="${x.day}: ${x.calls} calls"><div style="height:${Math.round((x.calls / maxCalls) * 100)}%"></div><span>${x.day.slice(5)}</span><b>${x.calls}</b></div>`)
    .join("");

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>Factory dashboard</title>
<style>
:root{color-scheme:light dark;--bg:#fcfcfb;--fg:#0b0b0b;--fg2:#52514e;--line:#dedcd6;--card:#f4f3ef;--accent:#2a78d6;--good:#0ca30c;--warn:#fab219;--bad:#d03b3b;--mute:#8a8984}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--fg:#f0efec;--fg2:#c3c2b7;--line:#3a3936;--card:#242422;--accent:#3987e5}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--accent)}h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line)}h3{font-size:14px;margin:12px 0 4px}
.top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;color:var(--fg2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:12px 0}
.tile{background:var(--card);border-radius:8px;padding:10px 12px}.tile .label{color:var(--fg2);font-size:12px}.tile .value{font-size:22px;font-weight:600;margin:2px 0}.tile .sub{color:var(--fg2);font-size:12px}
.meter{height:6px;border-radius:3px;background:var(--line);margin:4px 0}.meter div{height:6px;border-radius:3px;background:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;vertical-align:top;padding:5px 8px;border-bottom:1px solid var(--line)}th{color:var(--fg2);font-weight:500;font-size:12px}
.pill{display:inline-block;padding:0 7px;border-radius:9px;font-size:12px;white-space:nowrap;border:1px solid}.good{color:var(--good);border-color:var(--good)}.bad{color:var(--bad);border-color:var(--bad)}.warn{color:var(--warn);border-color:var(--warn)}.mute{color:var(--mute);border-color:var(--mute)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}code.act{white-space:pre-wrap;word-break:break-all;display:block;color:var(--fg2)}
.dim{color:var(--fg2)}.empty{color:var(--fg2);font-style:italic}.wrap{overflow-x:auto}
.blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}.blk{background:var(--card);border-radius:8px;padding:8px 12px}.blk h3{margin:4px 0 6px}.blk p{margin:0 0 6px}.item{padding:6px 0;border-top:1px solid var(--line)}
.queues{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.q{background:var(--card);border-radius:8px;padding:8px 12px}.q ol{margin:4px 0 0;padding-left:20px}.q li{margin:2px 0}.q li.held{color:var(--fg2)}
.bars{display:flex;gap:6px;align-items:flex-end;height:110px;margin:28px 0 28px}.bar{flex:1;max-width:60px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;position:relative}.bar div{width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:1px}.bar span{font-size:11px;color:var(--fg2);position:absolute;bottom:-18px}.bar b{font-size:11px;font-weight:500;position:absolute;top:-16px}
details{margin:8px 0}summary{cursor:pointer;color:var(--fg2)}
</style></head><body>
<h1>Factory dashboard${d.paused ? ' <span class="pill bad">&#10007; PAUSED</span>' : ""}</h1>
<div class="top"><span>target ${link(`https://github.com/${repo}`, repo)}</span><span>generated ${esc(d.generated_at.slice(0, 19).replace("T", " "))} UTC</span><span>window ${windowLinks}</span><span><a href="?format=json&hours=${d.window_hours}">json</a> · <a href="/status">status</a></span></div>
${d.github_error ? `<p class="pill bad">&#9888; GitHub unreachable: ${esc(d.github_error)}. Issue, PR and queue sections are empty; the run ledger is still live.</p>` : ""}
<div class="tiles">${tiles}</div>

<h2>Blocked: what needs a human, and what is just waiting</h2>
<p class="dim">&#9679; you: a human has to act. &#9675; waits: clears on its own (a dependency closes, a run finishes, the day rolls over).</p>
<div class="blocks">${blockedHtml}</div>

<h2>In flight</h2>
<div class="wrap">${table(["agent", "subject", "status", "dispatched", ""], flightRows)}</div>

<h2>Next: what the tick will do</h2>
<p class="dim">Next tick ${clock(d.next.next_tick_at)} UTC (${ago(d.next.next_tick_at, now)}). Candidate order is the tick's own: the first eligible item per agent is dispatched if quota and locks allow.</p>
<div class="queues">${queueHtml}</div>
<h3>Scheduled</h3>
<div class="wrap">${table(["agent", "when", "what"], schedRows)}</div>
<details><summary>Last tick ${esc(d.next.last_tick?.at ?? "never")}: ${(d.next.last_tick?.decisions ?? []).length} decisions</summary><ul>${decisions || "<li>none</li>"}</ul></details>

<h2>Happened: last ${d.window_hours} h</h2>
<h3>Target movement</h3>
<div class="wrap">${table(["when (UTC)", "", "what", "title", ""], movement)}</div>
<h3>Model calls per day</h3>
<div class="bars">${bars}</div>
<h3>Runs by agent</h3>
<div class="wrap">${table(["agent", ...statuses], countRows)}</div>
<h3>Runs</h3>
<div class="wrap">${table(["dispatched (UTC)", "agent", "subject", "trigger", "outcome", "took", "calls", ""], runRows)}</div>
<p class="dim">Read-only. Refreshes every 5 minutes. Times are UTC.</p>
</body></html>`;
}

// ------------------------------------------------------------------ I/O

export async function handleDashboard(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const now = new Date();
  const windowHours = clampWindow(url.searchParams.get("hours"));
  const since = new Date(now.getTime() - windowHours * 3600_000).toISOString();
  const floor = since < startOfUtcDay(now) ? since : startOfUtcDay(now);
  const [runs, lt, paused] = await Promise.all([
    env.DB.prepare("SELECT * FROM runs WHERE status IN ('dispatched','started') OR dispatched_at >= ? OR finished_at >= ? ORDER BY dispatched_at DESC LIMIT 500")
      .bind(floor, floor)
      .all<RunRow>(),
    env.DB.prepare("SELECT value FROM config WHERE key = 'last_tick'").first<{ value: string }>(),
    isPaused(env),
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
  const doc = buildDashboard({
    now, windowHours, paused, lastTick: lt?.value ?? null, dailyQuota: dailyQuota(env), maxOpenFactoryPrs: maxOpenFactoryPrs(env),
    runs: runs.results ?? [], openIssues, openPRs, updatedSince, targetRepo: env.TARGET_REPO, githubError,
  });
  const wantsHtml = url.searchParams.get("format") !== "json" && (req.headers.get("accept") ?? "").includes("text/html");
  const headers = { "cache-control": "public, max-age=60" };
  if (wantsHtml) return new Response(dashboardHtml(doc), { headers: { ...headers, "content-type": "text/html; charset=utf-8" } });
  const res = json(doc);
  res.headers.set("cache-control", headers["cache-control"]);
  return res;
}
