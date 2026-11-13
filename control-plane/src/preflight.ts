// PURE policy: no I/O, no clock reads. Unit-tested in test/preflight.test.ts.

export interface InFlight {
  runId: string;
  agent: string;
  subject: string;
  dispatchedAt: string;
  timeoutMinutes: number;
  reservedCalls?: number;
}
export interface RecentRun {
  agent: string;
  subject: string;
  dispatchedAt: string;
  /** Ledger status: dispatched, started, success, failed, timeout, denied or lost. */
  status: string;
}
export interface PRInfo {
  number: number;
  headRef: string;
  body: string | null;
  author: string;
  labels?: string[];
}
export interface IssueInfo {
  number: number;
  labels: string[];
  authorAssociation: string;
  body: string | null;
}
export interface TickState {
  paused: boolean;
  quota: { daily: number; usedToday: number; reservedInFlight: number };
  inFlight: InFlight[];
  /** Runs dispatched in the last ~26h (any status); used for hourly/daily cadence. */
  recentRuns?: RecentRun[];
  openPRs: PRInfo[];
  openIssues: IssueInfo[];
  agentSpecs: Record<string, { maxModelCalls: number; timeoutMinutes: number }>;
  maxOpenFactoryPrs: number;
  /** Open issues with no labels at all. */
  unlabeledIssues: number[];
}

export interface DispatchInputs {
  run_id: string;
  issue: string;
  pr: string;
  phase: string;
  dry_run: string;
}
export type Decision =
  | { kind: "dispatch"; agent: string; subject: string; inputs: DispatchInputs; reservedCalls: number }
  | { kind: "mark_lost"; runId: string }
  | { kind: "skip"; agent: string; reason: string };

const TRUSTED = new Set(["OWNER", "COLLABORATOR", "MEMBER"]);
export const STALE_GRACE_MIN = 10;

export function priorityOf(labels: string[]): number {
  for (const l of labels) {
    const m = /^priority:(\d)$/.exec(l);
    if (m) return Number(m[1]);
  }
  return 9;
}

export function parseDependencies(body: string | null): number[] {
  if (!body) return [];
  const out = new Set<number>();
  const re = /(?:depends\s+on|blocked\s+by)\s*:?\s*((?:#\d+(?:\s*(?:,|and|&)\s*)?)+)/gi;
  for (const m of body.matchAll(re)) {
    for (const n of m[1].matchAll(/#(\d+)/g)) out.add(Number(n[1]));
  }
  return [...out];
}

export function isDependabot(author: string): boolean {
  return /dependabot/i.test(author);
}

export function prReferences(pr: PRInfo, issue: number): boolean {
  if (new RegExp(`^(feat|fix)/${issue}(?!\\d)`).test(pr.headRef)) return true;
  return new RegExp(`#${issue}(?!\\d)`).test(pr.body ?? "");
}

/** The issue number in a factory head ref (`feat/N-...` or `fix/N-...`), else null. */
export function factoryHeadIssue(headRef: string): number | null {
  const m = /^(?:feat|fix)\/(\d+)-/.exec(headRef);
  return m ? Number(m[1]) : null;
}

function slug(s: string): string {
  return s.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

export function makeRunId(agent: string, subject: string, now: Date): string {
  const ts = now.toISOString().replace(/[-:T]/g, "").slice(0, 12);
  return `${agent}-${ts}-${slug(subject)}`;
}

export function blockReason(i: IssueInfo, openNumbers: Set<number>): string | null {
  if (i.labels.includes("needs-human")) return "needs-human";
  if (!TRUSTED.has(i.authorAssociation) && !i.labels.includes("factory:accepted")) return "author-not-trusted";
  const open = parseDependencies(i.body).filter((n) => openNumbers.has(n));
  if (open.length) return `blocked-by:${open.map((n) => `#${n}`).join(",")}`;
  return null;
}

export const BUILDER_TYPES = ["feature", "enhancement"];
export const BUG_TYPES = ["bug"];

/** An issue in the tick's candidate order with the reason it would be passed over (null = dispatchable). */
export interface Candidate {
  issue: IssueInfo;
  reason: string | null;
}

export const FAILURE_STATUSES = ["failed", "timeout", "lost"];
export const FAILURE_WINDOW_MS = 24 * 3600_000;
export const FAILURE_CAP = 3;
/** Minutes to wait after the 1st and the 2nd recent failure. */
export const BACKOFF_MIN = [60, 180];

const isoMinute = (ms: number): string => new Date(ms).toISOString().slice(0, 16) + "Z";

/**
 * Pure: per-subject failure backoff. Recent failures are the subject's failed, timed-out or lost runs from the
 * last 24 h that were dispatched after its latest success. Returns `retry-after:<ISO>` while waiting,
 * `failure-cap:<ISO>` at FAILURE_CAP failures, null when the subject may run.
 */
export function failureBackoff(subject: string, recent: RecentRun[], now: Date): string | null {
  const runs = recent.filter((r) => r.subject === subject);
  const lastSuccess = runs
    .filter((r) => r.status === "success")
    .map((r) => Date.parse(r.dispatchedAt))
    .reduce((a, b) => Math.max(a, b), -Infinity);
  const failures = runs
    .filter((r) => FAILURE_STATUSES.includes(r.status))
    .map((r) => Date.parse(r.dispatchedAt))
    .filter((t) => now.getTime() - t < FAILURE_WINDOW_MS && t > lastSuccess)
    .sort((a, b) => a - b);
  const n = failures.length;
  if (n === 0) return null;
  if (n >= FAILURE_CAP) return `failure-cap:${isoMinute(failures[0] + FAILURE_WINDOW_MS)}`;
  const retryAt = failures[n - 1] + BACKOFF_MIN[n - 1] * 60_000;
  return now.getTime() < retryAt ? `retry-after:${isoMinute(retryAt)}` : null;
}

/**
 * Pure: the ordered candidate list the tick walks for one agent (builder: feature and enhancement;
 * bug-fixer: bug), with each issue's skip reason. The tick dispatches the first candidate whose
 * reason is null; the dashboard shows the whole list as "what happens next".
 */
export function candidateQueue(
  state: Pick<TickState, "openIssues" | "openPRs" | "recentRuns">,
  types: string[],
  now: Date,
): Candidate[] {
  const openNumbers = new Set(state.openIssues.map((i) => i.number));
  const hasPR = (n: number) => state.openPRs.some((p) => prReferences(p, n));
  const builder = types.includes("feature");
  return state.openIssues
    .filter(
      (i) =>
        (i.labels.includes("status:backlog") || (i.labels.includes("status:plan-review") && i.labels.includes("plan:approved"))) &&
        i.labels.some((l) => types.includes(l)),
    )
    .sort((a, b) => priorityOf(a.labels) - priorityOf(b.labels) || a.number - b.number)
    .map((issue) => {
      let reason: string | null = hasPR(issue.number) ? "has-open-pr" : blockReason(issue, openNumbers);
      // risk:high is dispatched too: the pipeline stops after the plan and parks the issue in status:plan-review.
      if (builder && reason === null && !issue.labels.includes("risk:low") && !issue.labels.includes("risk:high")) reason = "awaiting-risk";
      if (reason === null) reason = failureBackoff(`issue:${issue.number}`, state.recentRuns ?? [], now);
      return { issue, reason };
    });
}

/** An open factory PR waiting for its one revise run, with the reason it would be passed over (null = dispatchable). */
export interface ReviseCandidate {
  pr: number;
  issue: number;
  agent: "builder" | "bug-fixer";
  reason: string | null;
}

/** Pure: open factory PRs labelled revise:requested (and not revise:attempted), by PR number. */
export function reviseCandidates(state: Pick<TickState, "openPRs" | "recentRuns">, now: Date): ReviseCandidate[] {
  const out: ReviseCandidate[] = [];
  for (const p of state.openPRs) {
    const labels = p.labels ?? [];
    const issue = factoryHeadIssue(p.headRef);
    if (issue === null || !labels.includes("revise:requested") || labels.includes("revise:attempted")) continue;
    // Never push to a PR a human parked, nor to a human's PR (the workflow would deny it and the denial is no backoff).
    if (labels.includes("needs-human") || !p.author.endsWith("[bot]")) continue;
    out.push({
      pr: p.number,
      issue,
      agent: p.headRef.startsWith("feat/") ? "builder" : "bug-fixer",
      reason: failureBackoff(`revise:${issue}`, state.recentRuns ?? [], now),
    });
  }
  return out.sort((a, b) => a.pr - b.pr);
}

/** Pure: an open bug at priority 1 or 2 that no human is parked on; while one exists the builder waits (bugs_first). */
export function urgentBugs(issues: IssueInfo[]): IssueInfo[] {
  // A bug parked on a human (needs-human) is nothing an agent can act on, so it does not hold features back.
  return issues.filter(
    (i) =>
      i.labels.includes("bug") &&
      !i.labels.includes("needs-human") &&
      (i.labels.includes("priority:1") || i.labels.includes("priority:2")),
  );
}

export function countFactoryPRs(prs: Pick<PRInfo, "headRef">[]): number {
  return prs.filter((p) => /^(feat|fix)\//.test(p.headRef)).length;
}

const MAX_BLOCKED_LISTED = 10;
// "#35:reason, #100:reason, ... , +N more": every blocked candidate in backlog order, bounded for D1 and the status table.
function formatBlocked(blocked: string[]): string {
  const shown = blocked.slice(0, MAX_BLOCKED_LISTED).join(", ");
  return blocked.length > MAX_BLOCKED_LISTED ? `${shown}, +${blocked.length - MAX_BLOCKED_LISTED} more` : shown;
}

export function planTick(state: TickState, now: Date): Decision[] {
  if (state.paused) return [];
  const decisions: Decision[] = [];
  const nowMs = now.getTime();

  const live: InFlight[] = [];
  let remaining = state.quota.daily - state.quota.usedToday - state.quota.reservedInFlight;
  for (const f of state.inFlight) {
    const deadline = Date.parse(f.dispatchedAt) + (f.timeoutMinutes + STALE_GRACE_MIN) * 60_000;
    if (deadline < nowMs) {
      decisions.push({ kind: "mark_lost", runId: f.runId });
      remaining += f.reservedCalls ?? 0;
    } else live.push(f);
  }
  const busy = new Set(live.map((f) => f.agent));
  const recent = state.recentRuns ?? [];

  const dispatch = (agent: string, subject: string, issue: number | null, pr: number | null, phase: string): boolean => {
    if (busy.has(agent)) {
      decisions.push({ kind: "skip", agent, reason: "in_flight" });
      return false;
    }
    const spec = state.agentSpecs[agent];
    if (!spec) {
      decisions.push({ kind: "skip", agent, reason: "no_spec" });
      return false;
    }
    if (spec.maxModelCalls > remaining) {
      decisions.push({ kind: "skip", agent, reason: "quota" });
      return false;
    }
    remaining -= spec.maxModelCalls;
    busy.add(agent);
    decisions.push({
      kind: "dispatch",
      agent,
      subject,
      reservedCalls: spec.maxModelCalls,
      inputs: {
        run_id: makeRunId(agent, subject, now),
        issue: issue === null ? "" : String(issue),
        pr: pr === null ? "" : String(pr),
        phase,
        dry_run: "false",
      },
    });
    return true;
  };

  // incident-responder: once per day, 06:00 UTC hour window
  if (now.getUTCHours() === 6) {
    const date = now.toISOString().slice(0, 10);
    if (!recent.some((r) => r.agent === "incident-responder" && r.subject === date)) {
      dispatch("incident-responder", date, null, null, "respond");
    }
    // security-triage: same daily window, its own quota reservation
    if (!recent.some((r) => r.agent === "security-triage" && r.subject === date)) {
      dispatch("security-triage", date, null, null, "triage");
    }
  }

  // reviewer: hourly for Dependabot PRs
  // The reviewer needs a concrete PR: without one it reviews nothing and still spends its model calls.
  // Oldest (lowest number) first; the subject stays "dependabot" so the cadence and lock are per loop, not per PR.
  // needs-human marks a PR the pipeline could not handle (for example a broken lockfile); leave it to the owner.
  const dependabotPRs = state.openPRs
    .filter((p) => isDependabot(p.author) && !(p.labels ?? []).includes("needs-human"))
    .map((p) => p.number);
  if (dependabotPRs.length) {
    const last = recent
      .filter((r) => r.agent === "reviewer" && r.subject === "dependabot")
      .map((r) => Date.parse(r.dispatchedAt))
      .reduce((a, b) => Math.max(a, b), 0);
    if (nowMs - last >= 50 * 60_000) dispatch("reviewer", "dependabot", null, Math.min(...dependabotPRs), "review");
  }

  // triage sweep
  if (state.unlabeledIssues.length) {
    const n = [...state.unlabeledIssues].sort((a, b) => a - b)[0];
    dispatch("triage", `issue:${n}`, n, null, "triage");
  }

  // Walk the candidate order until the first dispatchable issue; everything before it is reported as blocked.
  const pick = (queue: Candidate[]): { chosen: IssueInfo | null; blocked: string[] } => {
    const blocked: string[] = [];
    for (const c of queue) {
      if (c.reason === null) return { chosen: c.issue, blocked };
      blocked.push(`#${c.issue.number}:${c.reason}`);
    }
    return { chosen: null, blocked };
  };

  // Revise first: finishing a PR that is already open ignores backpressure and bugs_first, not quota or busy.
  const revises = reviseCandidates(state, now);
  const dispatchRevise = (agent: string): boolean => {
    const r = revises.find((c) => c.agent === agent && c.reason === null);
    return r ? dispatch(agent, `revise:${r.issue}`, r.issue, r.pr, "revise") : false;
  };

  // bug-fixer
  const bugRevised = dispatchRevise("bug-fixer");
  const bugs = bugRevised ? [] : candidateQueue(state, BUG_TYPES, now);
  if (bugs.length) {
    const { chosen, blocked } = pick(bugs);
    if (chosen) dispatch("bug-fixer", `issue:${chosen.number}`, chosen.number, null, "");
    else decisions.push({ kind: "skip", agent: "bug-fixer", reason: `no_eligible_bug (${formatBlocked(blocked)})` });
  }

  // builder
  const featureRevised = dispatchRevise("builder");
  const features = featureRevised ? [] : candidateQueue(state, BUILDER_TYPES, now);
  if (features.length) {
    if (countFactoryPRs(state.openPRs) >= state.maxOpenFactoryPrs) {
      decisions.push({ kind: "skip", agent: "builder", reason: "backpressure" });
    } else if (urgentBugs(state.openIssues).length) {
      decisions.push({ kind: "skip", agent: "builder", reason: "bugs_first" });
    } else {
      const { chosen, blocked } = pick(features);
      if (chosen) dispatch("builder", `issue:${chosen.number}`, chosen.number, null, "");
      else decisions.push({ kind: "skip", agent: "builder", reason: `no_eligible_feature (${formatBlocked(blocked)})` });
    }
  }

  return decisions;
}
