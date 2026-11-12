import { describe, expect, it } from "vitest";
import { planTick, parseDependencies, failureBackoff, type TickState, type IssueInfo, type RecentRun } from "../src/preflight";
import { AGENT_SPECS } from "../src/config";

const NOW = new Date("2026-10-21T10:07:00Z");
const issue = (n: number, labels: string[], extra: Partial<IssueInfo> = {}): IssueInfo => ({
  number: n, labels, authorAssociation: "OWNER", body: "", ...extra,
});
const feat = (n: number, extra: string[] = [], o: Partial<IssueInfo> = {}) =>
  issue(n, ["status:backlog", "feature", "risk:low", ...extra], o);
const base = (over: Partial<TickState> = {}): TickState => ({
  paused: false,
  quota: { daily: 400, usedToday: 0, reservedInFlight: 0 },
  inFlight: [], recentRuns: [], openPRs: [], openIssues: [],
  agentSpecs: AGENT_SPECS, maxOpenFactoryPrs: 3, unlabeledIssues: [], ...over,
});
const dispatched = (d: ReturnType<typeof planTick>) => d.filter((x) => x.kind === "dispatch") as any[];

describe("planTick", () => {
  it("returns nothing when paused", () => {
    expect(planTick(base({ paused: true, openIssues: [feat(1)] }), NOW)).toEqual([]);
  });
  it("marks stale in-flight lost and frees the agent", () => {
    const d = planTick(base({
      inFlight: [{ runId: "r1", agent: "builder", subject: "issue:1", dispatchedAt: "2026-10-21T08:00:00Z", timeoutMinutes: 60, reservedCalls: 100 }],
      openIssues: [feat(2)],
    }), NOW);
    expect(d[0]).toEqual({ kind: "mark_lost", runId: "r1" });
    expect(dispatched(d)[0].subject).toBe("issue:2");
  });
  it("keeps non-stale in flight and allows one per agent", () => {
    const d = planTick(base({
      inFlight: [{ runId: "r1", agent: "builder", subject: "issue:1", dispatchedAt: "2026-10-21T10:00:00Z", timeoutMinutes: 60 }],
      openIssues: [feat(2)],
    }), NOW);
    expect(dispatched(d)).toHaveLength(0);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "in_flight" });
  });
  it("reserves quota per dispatch", () => {
    const st = base({ quota: { daily: 400, usedToday: 0, reservedInFlight: 0 }, unlabeledIssues: [5], openIssues: [issue(5, []), feat(1)] });
    st.quota = { daily: 110, usedToday: 0, reservedInFlight: 0 }; // triage 20 + builder 100 > 110
    const d = planTick(st, NOW);
    expect(dispatched(d).map((x) => x.agent)).toEqual(["triage"]);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "quota" });
  });
  it("applies backpressure on open factory PRs", () => {
    const prs = [1, 2, 3].map((n) => ({ number: 10 + n, headRef: `feat/${n}-x`, body: "", author: "app" }));
    const d = planTick(base({ openPRs: prs, openIssues: [feat(9)] }), NOW);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "backpressure" });
  });
  it("does bugs first: urgent bug blocks builder and bug-fixer dispatches", () => {
    const d = planTick(base({ openIssues: [feat(1), issue(2, ["status:backlog", "bug", "priority:1"])] }), NOW);
    expect(dispatched(d).map((x) => x.agent)).toEqual(["bug-fixer"]);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "bugs_first" });
  });
  it("an urgent bug waiting on a human does not block the builder", () => {
    const d = planTick(base({ openIssues: [feat(1), issue(2, ["status:backlog", "bug", "priority:1", "needs-human"])] }), NOW);
    expect(dispatched(d).map((x) => x.agent)).toEqual(["builder"]);
    expect(d).not.toContainEqual({ kind: "skip", agent: "builder", reason: "bugs_first" });
    expect(d).toContainEqual({ kind: "skip", agent: "bug-fixer", reason: "no_eligible_bug (#2:needs-human)" });
  });
  it("skips bug with an open PR referencing it", () => {
    const d = planTick(base({
      openIssues: [issue(2, ["status:backlog", "bug", "priority:3"])],
      openPRs: [{ number: 7, headRef: "fix/2-thing", body: "", author: "a" }],
    }), NOW);
    expect(dispatched(d)).toHaveLength(0);
  });
  it("respects dependency chain", () => {
    expect(parseDependencies("Depends on #1, #2\nBlocked by #3")).toEqual([1, 2, 3]);
    const d = planTick(base({ openIssues: [feat(2, [], { body: "Depends on #1" }), feat(1)] }), NOW);
    expect(dispatched(d)[0].subject).toBe("issue:1");
    const d2 = planTick(base({ openIssues: [feat(2, [], { body: "Depends on #1" }), issue(1, ["status:in-progress"])] }), NOW);
    expect(dispatched(d2)).toHaveLength(0);
    const d3 = planTick(base({ openIssues: [feat(2, [], { body: "Blocked by #99" })] }), NOW); // 99 closed
    expect(dispatched(d3)[0].subject).toBe("issue:2");
  });
  it("orders by priority then number", () => {
    const d = planTick(base({ openIssues: [feat(1, ["priority:3"]), feat(5, ["priority:1"]), feat(4, ["priority:1"])] }), NOW);
    expect(dispatched(d)[0].subject).toBe("issue:4");
  });
  it("applies author gate", () => {
    const stranger = feat(1, [], { authorAssociation: "NONE" });
    expect(dispatched(planTick(base({ openIssues: [stranger] }), NOW))).toHaveLength(0);
    expect(dispatched(planTick(base({ openIssues: [feat(1, ["factory:accepted"], { authorAssociation: "NONE" })] }), NOW))).toHaveLength(1);
    expect(dispatched(planTick(base({ openIssues: [feat(1, ["needs-human"])] }), NOW))).toHaveLength(0);
  });
  it("applies risk gate: unrated waits, risk:high is dispatched", () => {
    const unrated = issue(1, ["status:backlog", "feature"]);
    const d = planTick(base({ openIssues: [unrated] }), NOW);
    expect(dispatched(d)).toHaveLength(0);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "no_eligible_feature (#1:awaiting-risk)" });
    const high = issue(1, ["status:backlog", "feature", "risk:high"]);
    const dh = dispatched(planTick(base({ openIssues: [high] }), NOW));
    expect(dh).toHaveLength(1);
    expect(dh[0].subject).toBe("issue:1");
  });
  it("resumes a plan-approved feature from status:plan-review, ordered with the backlog", () => {
    const resumed = issue(3, ["status:plan-review", "feature", "risk:high", "plan:approved", "priority:1"]);
    const d = planTick(base({ openIssues: [feat(1, ["priority:3"]), feat(2, ["priority:1"]), resumed] }), NOW);
    expect(dispatched(d)[0].subject).toBe("issue:2");
    const d2 = planTick(base({ openIssues: [feat(1, ["priority:3"]), resumed] }), NOW);
    expect(dispatched(d2)[0].subject).toBe("issue:3");
    expect(dispatched(d2)[0].inputs.phase).toBe("");
  });
  it("status:plan-review without plan:approved is not a candidate", () => {
    const parked = issue(1, ["status:plan-review", "feature", "risk:high"]);
    const d = planTick(base({ openIssues: [parked] }), NOW);
    expect(dispatched(d)).toHaveLength(0);
    expect(d.filter((x) => x.kind === "skip" && x.agent === "builder")).toHaveLength(0);
  });
  it("a plan-approved resume still honours needs-human", () => {
    const held = issue(1, ["status:plan-review", "feature", "risk:high", "plan:approved", "needs-human"]);
    const d = planTick(base({ openIssues: [held] }), NOW);
    expect(dispatched(d)).toHaveLength(0);
    expect(d).toContainEqual({ kind: "skip", agent: "builder", reason: "no_eligible_feature (#1:needs-human)" });
  });
  it("resumes a plan-approved bug via the bug-fixer, and ignores an unapproved one", () => {
    const approved = issue(4, ["status:plan-review", "bug", "plan:approved"]);
    const d = dispatched(planTick(base({ openIssues: [approved] }), NOW));
    expect(d.map((x) => x.agent)).toEqual(["bug-fixer"]);
    expect(d[0].subject).toBe("issue:4");
    const parked = issue(4, ["status:plan-review", "bug"]);
    expect(dispatched(planTick(base({ openIssues: [parked] }), NOW))).toHaveLength(0);
    const held = issue(4, ["status:plan-review", "bug", "plan:approved", "needs-human"]);
    expect(planTick(base({ openIssues: [held] }), NOW)).toContainEqual({ kind: "skip", agent: "bug-fixer", reason: "no_eligible_bug (#4:needs-human)" });
  });
  it("skip reason lists every blocked feature in order", () => {
    const d = planTick(base({
      openIssues: [
        issue(35, ["status:backlog", "feature", "priority:1"]),
        feat(100, [], { authorAssociation: "NONE" }),
        feat(106, ["needs-human"]),
        feat(107, [], { body: "Blocked by #106" }),
      ],
    }), NOW);
    expect(d).toContainEqual({
      kind: "skip", agent: "builder",
      reason: "no_eligible_feature (#35:awaiting-risk, #100:author-not-trusted, #106:needs-human, #107:blocked-by:#106)",
    });
  });
  it("skip reason lists every blocked bug", () => {
    const d = planTick(base({
      openIssues: [
        issue(1, ["status:backlog", "bug"], { authorAssociation: "NONE" }),
        issue(2, ["status:backlog", "bug", "needs-human"]),
      ],
    }), NOW);
    expect(d).toContainEqual({ kind: "skip", agent: "bug-fixer", reason: "no_eligible_bug (#1:author-not-trusted, #2:needs-human)" });
  });
  it("skip reason caps at 10 entries with +N more", () => {
    const issues = Array.from({ length: 13 }, (_, i) => feat(i + 1, [], { authorAssociation: "NONE" }));
    const d = planTick(base({ openIssues: issues }), NOW);
    const skip = d.find((x) => x.kind === "skip" && x.agent === "builder") as any;
    expect(skip.reason).toBe(
      `no_eligible_feature (${Array.from({ length: 10 }, (_, i) => `#${i + 1}:author-not-trusted`).join(", ")}, +3 more)`,
    );
  });
  it("triage sweep for unlabeled issues", () => {
    const d = planTick(base({ unlabeledIssues: [8, 3], openIssues: [issue(3, []), issue(8, [])] }), NOW);
    expect(dispatched(d)[0]).toMatchObject({ agent: "triage", subject: "issue:3" });
  });
  it("dependabot reviewer hourly", () => {
    const prs = [
      { number: 7, headRef: "dependabot/npm/y", body: "", author: "app/dependabot" },
      { number: 0, headRef: "dependabot/npm/broken", body: "", author: "app/dependabot", labels: ["needs-human"] },
      { number: 1, headRef: "dependabot/npm/x", body: "", author: "app/dependabot" },
      { number: 3, headRef: "feat/3-x", body: "", author: "app/software-factory-agent-bot" },
    ];
    expect(dispatched(planTick(base({ openPRs: prs }), NOW))[0]).toMatchObject({
      agent: "reviewer", subject: "dependabot", inputs: { pr: "1" },
    });
    const recent = [{ agent: "reviewer", subject: "dependabot", dispatchedAt: "2026-10-21T09:30:00Z", status: "success" }];
    expect(dispatched(planTick(base({ openPRs: prs, recentRuns: recent }), NOW))).toHaveLength(0);
    const old = [{ agent: "reviewer", subject: "dependabot", dispatchedAt: "2026-10-21T09:00:00Z", status: "success" }];
    expect(dispatched(planTick(base({ openPRs: prs, recentRuns: old }), NOW))).toHaveLength(1);
    expect(dispatched(planTick(base({}), NOW))).toHaveLength(0);
  });
  it("incident responder once daily at 06:00 UTC", () => {
    const six = new Date("2026-10-21T06:05:00Z");
    const both = dispatched(planTick(base(), six));
    expect(both.map((d) => d.agent)).toEqual(["incident-responder", "security-triage"]);
    expect(both[0]).toMatchObject({ agent: "incident-responder", subject: "2026-10-21" });
    expect(both[1]).toMatchObject({ agent: "security-triage", subject: "2026-10-21" });
    expect(both[1].inputs.phase).toBe("triage");
    const recent = [{ agent: "incident-responder", subject: "2026-10-21", dispatchedAt: "2026-10-21T06:00:00Z", status: "success" }];
    expect(dispatched(planTick(base({ recentRuns: recent }), six)).map((d) => d.agent)).toEqual(["security-triage"]);
    const secRecent = [{ agent: "security-triage", subject: "2026-10-21", dispatchedAt: "2026-10-21T06:00:00Z", status: "success" }];
    expect(dispatched(planTick(base({ recentRuns: secRecent }), six)).map((d) => d.agent)).toEqual(["incident-responder"]);
    expect(dispatched(planTick(base({ recentRuns: [...recent, ...secRecent] }), six))).toHaveLength(0);
    expect(dispatched(planTick(base(), NOW))).toHaveLength(0);
  });
});

const minAgo = (m: number) => new Date(NOW.getTime() - m * 60_000).toISOString();
const rr = (status: string, minutesAgo: number, subject = "issue:7", agent = "builder"): RecentRun => ({
  agent, subject, status, dispatchedAt: minAgo(minutesAgo),
});

describe("failureBackoff", () => {
  it("is null with no failures", () => {
    expect(failureBackoff("issue:7", [], NOW)).toBeNull();
  });
  it("waits 60 minutes after one failure", () => {
    expect(failureBackoff("issue:7", [rr("failed", 30)], NOW)).toBe("retry-after:2026-10-21T10:37Z");
    expect(failureBackoff("issue:7", [rr("failed", 61)], NOW)).toBeNull();
  });
  it("waits 180 minutes after the latest of two failures", () => {
    expect(failureBackoff("issue:7", [rr("timeout", 300), rr("lost", 120)], NOW)).toBe("retry-after:2026-10-21T11:07Z");
    expect(failureBackoff("issue:7", [rr("failed", 400), rr("failed", 181)], NOW)).toBeNull();
  });
  it("caps at three failures until 24 h after the oldest", () => {
    const r = failureBackoff("issue:7", [rr("failed", 600), rr("failed", 300), rr("failed", 10)], NOW);
    expect(r).toBe("failure-cap:2026-10-22T00:07Z");
  });
  it("a success after the failures resets the count", () => {
    expect(failureBackoff("issue:7", [rr("failed", 200), rr("failed", 190), rr("success", 120, "issue:7", "bug-fixer")], NOW)).toBeNull();
    expect(failureBackoff("issue:7", [rr("failed", 200), rr("success", 120), rr("failed", 30)], NOW)).toBe("retry-after:2026-10-21T10:37Z");
  });
  it("ignores denied, in-flight and other subjects", () => {
    const runs = [rr("denied", 5), rr("started", 5), rr("dispatched", 5), rr("failed", 5, "issue:8")];
    expect(failureBackoff("issue:7", runs, NOW)).toBeNull();
  });
  it("ignores failures older than 24 h", () => {
    expect(failureBackoff("issue:7", [rr("failed", 24 * 60 + 1), rr("failed", 25 * 60), rr("failed", 26 * 60)], NOW)).toBeNull();
  });
});

describe("planTick failure backoff", () => {
  it("walks past a backed-off issue to the next candidate", () => {
    const d = planTick(base({ openIssues: [feat(7), feat(8)], recentRuns: [rr("failed", 30)] }), NOW);
    expect(dispatched(d).map((x) => x.subject)).toEqual(["issue:8"]);
  });
  it("redispatches after the backoff", () => {
    const d = planTick(base({ openIssues: [feat(7), feat(8)], recentRuns: [rr("failed", 61)] }), NOW);
    expect(dispatched(d).map((x) => x.subject)).toEqual(["issue:7"]);
  });
  it("reports the reason in the skip when nothing else is eligible", () => {
    const d = planTick(base({ openIssues: [feat(7)], recentRuns: [rr("failed", 30)] }), NOW);
    expect(d.find((x) => x.kind === "skip" && x.agent === "builder")).toEqual({
      kind: "skip", agent: "builder", reason: "no_eligible_feature (#7:retry-after:2026-10-21T10:37Z)",
    });
  });
  it("stops at the cap", () => {
    const d = planTick(base({ openIssues: [feat(7)], recentRuns: [rr("failed", 600), rr("failed", 300), rr("failed", 10)] }), NOW);
    expect(dispatched(d)).toEqual([]);
    expect(JSON.stringify(d)).toContain("#7:failure-cap:2026-10-22T00:07Z");
  });
});
