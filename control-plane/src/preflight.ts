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
}
export interface PRInfo {
  number: number;
  headRef: string;
  body: string | null;
  author: string;
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
const STALE_GRACE_MIN = 10;

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

function slug(s: string): string {
  return s.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}

export function makeRunId(agent: string, subject: string, now: Date): string {
  const ts = now.toISOString().replace(/[-:T]/g, "").slice(0, 12);
  return `${agent}-${ts}-${slug(subject)}`;
}

function blockReason(i: IssueInfo, openNumbers: Set<number>): string | null {
  if (i.labels.includes("needs-human")) return "needs-human";
  if (!TRUSTED.has(i.authorAssociation) && !i.labels.includes("factory:accepted")) return "author-not-trusted";
  const open = parseDependencies(i.body).filter((n) => openNumbers.has(n));
  if (open.length) return `blocked-by:${open.map((n) => `#${n}`).join(",")}`;
  return null;
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
  }

  // reviewer: hourly for Dependabot PRs
  if (state.openPRs.some((p) => isDependabot(p.author))) {
    const last = recent
      .filter((r) => r.agent === "reviewer" && r.subject === "dependabot")
      .map((r) => Date.parse(r.dispatchedAt))
      .reduce((a, b) => Math.max(a, b), 0);
    if (nowMs - last >= 50 * 60_000) dispatch("reviewer", "dependabot", null, null, "review");
  }

  // triage sweep
  if (state.unlabeledIssues.length) {
    const n = [...state.unlabeledIssues].sort((a, b) => a - b)[0];
    dispatch("triage", `issue:${n}`, n, null, "triage");
  }

  const openNumbers = new Set(state.openIssues.map((i) => i.number));
  const backlog = (types: string[]) =>
    state.openIssues
      .filter((i) => i.labels.includes("status:backlog") && i.labels.some((l) => types.includes(l)))
      .sort((a, b) => priorityOf(a.labels) - priorityOf(b.labels) || a.number - b.number);
  const hasPR = (n: number) => state.openPRs.some((p) => prReferences(p, n));

  // bug-fixer
  const bugs = backlog(["bug"]);
  if (bugs.length) {
    let chosen: IssueInfo | null = null;
    let why = "";
    for (const b of bugs) {
      const r = hasPR(b.number) ? "has-open-pr" : blockReason(b, openNumbers);
      if (r === null) {
        chosen = b;
        break;
      }
      why ||= `#${b.number}:${r}`;
    }
    if (chosen) dispatch("bug-fixer", `issue:${chosen.number}`, chosen.number, null, "");
    else decisions.push({ kind: "skip", agent: "bug-fixer", reason: `no_eligible_bug (${why})` });
  }

  // builder
  const features = backlog(["feature", "enhancement"]);
  if (features.length) {
    const factoryPrs = state.openPRs.filter((p) => /^(feat|fix)\//.test(p.headRef)).length;
    const urgentBug = state.openIssues.some(
      (i) => i.labels.includes("bug") && (i.labels.includes("priority:1") || i.labels.includes("priority:2")),
    );
    if (factoryPrs >= state.maxOpenFactoryPrs) {
      decisions.push({ kind: "skip", agent: "builder", reason: "backpressure" });
    } else if (urgentBug) {
      decisions.push({ kind: "skip", agent: "builder", reason: "bugs_first" });
    } else {
      let chosen: IssueInfo | null = null;
      let why = "";
      for (const f of features) {
        let r: string | null = hasPR(f.number) ? "has-open-pr" : blockReason(f, openNumbers);
        if (r === null && !f.labels.includes("risk:low") && !f.labels.includes("plan:approved")) r = "awaiting-risk-or-plan-approval";
        if (r === null) {
          chosen = f;
          break;
        }
        why ||= `#${f.number}:${r}`;
      }
      if (chosen) dispatch("builder", `issue:${chosen.number}`, chosen.number, null, "");
      else decisions.push({ kind: "skip", agent: "builder", reason: `no_eligible_feature (${why})` });
    }
  }

  return decisions;
}
