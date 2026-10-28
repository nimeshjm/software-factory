import { describe, expect, it } from "vitest";
import { planTick, parseDependencies, type TickState, type IssueInfo } from "../src/preflight";
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
  it("applies risk/plan gate", () => {
    const high = issue(1, ["status:backlog", "feature", "risk:high"]);
    expect(dispatched(planTick(base({ openIssues: [high] }), NOW))).toHaveLength(0);
    const approved = issue(1, ["status:backlog", "feature", "risk:high", "plan:approved"]);
    expect(dispatched(planTick(base({ openIssues: [approved] }), NOW))).toHaveLength(1);
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
    const recent = [{ agent: "reviewer", subject: "dependabot", dispatchedAt: "2026-10-21T09:30:00Z" }];
    expect(dispatched(planTick(base({ openPRs: prs, recentRuns: recent }), NOW))).toHaveLength(0);
    const old = [{ agent: "reviewer", subject: "dependabot", dispatchedAt: "2026-10-21T09:00:00Z" }];
    expect(dispatched(planTick(base({ openPRs: prs, recentRuns: old }), NOW))).toHaveLength(1);
    expect(dispatched(planTick(base({}), NOW))).toHaveLength(0);
  });
  it("incident responder once daily at 06:00 UTC", () => {
    const six = new Date("2026-10-21T06:05:00Z");
    expect(dispatched(planTick(base(), six))[0]).toMatchObject({ agent: "incident-responder", subject: "2026-10-21" });
    const recent = [{ agent: "incident-responder", subject: "2026-10-21", dispatchedAt: "2026-10-21T06:00:00Z" }];
    expect(dispatched(planTick(base({ recentRuns: recent }), six))).toHaveLength(0);
    expect(dispatched(planTick(base(), NOW))).toHaveLength(0);
  });
});
