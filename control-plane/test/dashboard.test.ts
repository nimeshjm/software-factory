import { describe, expect, it } from "vitest";
import { buildDashboard, clampWindow, dashboardHtml, nextTickAt, type DashboardInput } from "../src/dashboard";
import type { GhIssue, GhPR } from "../src/github";
import { candidateQueue, planTick, type TickState } from "../src/preflight";
import { AGENT_SPECS } from "../src/config";
import type { RunRow } from "../src/types";

const NOW = new Date("2026-11-12T13:40:00Z");
const REPO = "o/target";

const run = (o: Partial<RunRow>): RunRow => ({
  id: "r", agent: "builder", phase: null, subject: "issue:1", trigger: "cron", issue_number: null, pr_number: null,
  dispatched_at: "2026-11-12T13:30:00Z", started_at: null, finished_at: null, status: "dispatched",
  stop_reason: null, model_calls: 0, reserved_calls: 0, workflow_run_url: null, ...o,
});
const issue = (number: number, labels: string[], o: Partial<GhIssue> = {}): GhIssue => ({
  number, title: `Issue ${number}`, body: "", labels, authorAssociation: "OWNER", state: "open", author: "owner",
  createdAt: null, updatedAt: null, closedAt: null, htmlUrl: `https://github.com/${REPO}/issues/${number}`, isPullRequest: false, mergedAt: null, ...o,
});
const pr = (number: number, headRef: string, o: Partial<GhPR> = {}): GhPR => ({
  number, title: `PR ${number}`, body: "", author: "app/bot", headRef, labels: [], isDraft: false,
  createdAt: null, updatedAt: null, htmlUrl: `https://github.com/${REPO}/pull/${number}`, ...o,
});
const input = (o: Partial<DashboardInput> = {}): DashboardInput => ({
  now: NOW, windowHours: 48, paused: false, lastTick: null, dailyQuota: 400, maxOpenFactoryPrs: 3,
  runs: [], openIssues: [], openPRs: [], updatedSince: [], targetRepo: REPO, githubError: null, ...o,
});

describe("buildDashboard", () => {
  it("quota, window and empty state", () => {
    const d = buildDashboard(input());
    expect(d.today).toEqual({ model_calls: 0, reserved: 0, quota: 400, remaining: 400, resets_at: "2026-11-13T00:00:00.000Z" });
    expect(d.window_start).toBe("2026-11-10T13:40:00.000Z");
    expect(d.next.next_tick_at).toBe("2026-11-12T13:45:00.000Z");
    expect(d.blocked).toEqual([]);
    expect(d.in_flight).toEqual([]);
    expect(d.happened.calls_by_day.map((x) => x.day)).toEqual(["2026-11-10", "2026-11-11", "2026-11-12"]);
    expect(d.next.queue.map((q) => q.agent)).toEqual(["triage", "bug-fixer", "builder"]);
  });

  it("happened: runs in the window, counts, calls per day, target movement", () => {
    const d = buildDashboard(
      input({
        runs: [
          run({ id: "old", status: "success", dispatched_at: "2026-11-09T10:00:00Z", finished_at: "2026-11-09T10:30:00Z", model_calls: 50 }),
          run({ id: "a", agent: "reviewer", subject: "pr:126", status: "success", dispatched_at: "2026-11-12T13:11:00Z", started_at: "2026-11-12T13:11:20Z", finished_at: "2026-11-12T13:12:20Z", model_calls: 12, pr_number: 126, trigger: "webhook:pull_request_review" }),
          run({ id: "b", agent: "bug-fixer", subject: "issue:118", status: "failed", stop_reason: "exception", dispatched_at: "2026-11-11T09:00:00Z", finished_at: "2026-11-11T09:40:00Z", model_calls: 30 }),
          run({ id: "c", agent: "bug-fixer", subject: "issue:118", status: "success", dispatched_at: "2026-11-11T10:00:00Z", finished_at: "2026-11-11T10:40:00Z", model_calls: 35 }),
          run({ id: "f", status: "started", reserved_calls: 100, started_at: "2026-11-12T13:30:40Z", subject: "issue:107", workflow_run_url: "https://gh/run/1" }),
        ],
        updatedSince: [
          issue(118, ["status:done"], { state: "closed", closedAt: "2026-11-12T13:12:30Z" }),
          issue(126, [], { isPullRequest: true, state: "closed", closedAt: "2026-11-12T13:12:00Z", mergedAt: "2026-11-12T13:12:00Z", author: "app/bot" }),
          issue(90, [], { isPullRequest: true, state: "closed", closedAt: "2026-11-12T08:00:00Z", author: "dependabot[bot]" }),
          issue(5, [], { state: "closed", closedAt: "2026-11-01T00:00:00Z" }),
        ],
      }),
    );
    expect(d.happened.runs.map((r) => r.run_id)).toEqual(["f", "a", "c", "b"]);
    expect(d.happened.runs[1].duration_min).toBe(1);
    expect(d.happened.counts).toEqual({ builder: { in_flight: 1 }, reviewer: { success: 1 }, "bug-fixer": { failed: 1, success: 1 } });
    expect(d.happened.calls_by_day).toEqual([{ day: "2026-11-10", calls: 0 }, { day: "2026-11-11", calls: 65 }, { day: "2026-11-12", calls: 12 }]);
    expect(d.today).toMatchObject({ model_calls: 12, reserved: 100, remaining: 288 });
    expect(d.happened.target.closed_issues.map((i) => i.number)).toEqual([118]);
    expect(d.happened.target.merged_prs.map((p) => p.number)).toEqual([126]);
    expect(d.happened.target.closed_prs.map((p) => p.number)).toEqual([90]);
    // the failed 118 run was followed by a success on the same subject, so it is not a blocker
    expect(d.blocked.map((b) => b.kind)).toEqual([]);
    expect(d.in_flight).toEqual([
      expect.objectContaining({ run_id: "f", subject: "issue:107", age_min: 10, overdue: false, workflow_run_url: "https://gh/run/1" }),
    ]);
  });

  it("blocked: every kind, in severity order, with the human flag", () => {
    const d = buildDashboard(
      input({
        paused: true,
        runs: [
          run({ id: "stale", agent: "triage", subject: "issue:3", status: "started", dispatched_at: "2026-11-12T12:00:00Z" }),
          run({ id: "dead", agent: "builder", subject: "issue:50", status: "lost", stop_reason: "timeout_no_report", dispatched_at: "2026-11-12T08:00:00Z", finished_at: "2026-11-12T09:10:00Z" }),
        ],
        openIssues: [
          issue(50, ["feature", "status:in-progress", "risk:low"]),
          issue(60, ["feature", "status:in-review"]),
          issue(116, ["feature", "status:plan-review", "risk:high"]),
          issue(117, ["feature", "status:backlog", "needs-human", "risk:low"]),
          issue(100, ["feature", "status:backlog"]),
          issue(101, ["feature", "status:backlog", "risk:low"], { authorAssociation: "NONE" }),
          issue(102, ["feature", "status:backlog", "risk:low"], { body: "Depends on #116" }),
          issue(119, ["bug", "status:backlog", "priority:2", "needs-human"]),
          issue(125, ["bug", "status:backlog", "priority:2"]),
          issue(130, ["feature", "status:backlog", "risk:low"]),
        ],
        openPRs: [pr(88, "dependabot/npm_and_yarn/vitest", { author: "dependabot[bot]", labels: ["autonomy:lit"] }), pr(99, "fix/9-x", { labels: ["needs-human"] })],
      }),
    );
    expect(d.blocked.map((b) => [b.kind, b.subject, b.needs_human])).toEqual([
      ["paused", "factory", true],
      ["overdue-run", "run:stale", false],
      ["stuck-in-progress", "issue:50", true],
      ["orphan-in-review", "issue:60", true],
      ["failed-run", "run:dead", true],
      ["plan-review", "issue:116", true],
      ["needs-human", "issue:117", true],
      ["needs-human", "issue:119", true],
      ["awaiting-risk", "issue:100", true],
      ["author-not-trusted", "issue:101", true],
      ["blocked-by", "issue:102", false],
      ["lit-pr", "pr:88", true],
      ["needs-human-pr", "pr:99", true],
      ["bugs-first", "factory", false],
    ]);
    expect(d.blocked.find((b) => b.kind === "stuck-in-progress")?.action).toBe(`gh issue edit 50 -R ${REPO} --remove-label status:in-progress --add-label status:backlog`);
    expect(d.blocked.find((b) => b.kind === "bugs-first")?.title).toBe("Open bug #125 at priority 1 or 2");
    const builder = d.next.queue.find((q) => q.agent === "builder")!;
    expect(builder.gate).toBe("bugs_first");
    expect(builder.items.map((i) => [i.number, i.reason])).toEqual([
      [100, "awaiting-risk"], [101, "author-not-trusted"], [102, "blocked-by:#116"], [117, "needs-human"], [130, null],
    ]);
    const bugs = d.next.queue.find((q) => q.agent === "bug-fixer")!;
    expect(bugs.items.map((i) => [i.number, i.eligible])).toEqual([[119, false], [125, true]]);
    expect(bugs.gate).toBeNull();
  });

  it("a manual rerun that succeeded clears the failed-run of the cron run on the same issue", () => {
    const d = buildDashboard(
      input({
        runs: [
          run({ id: "cron", subject: "issue:118", issue_number: 118, status: "failed", dispatched_at: "2026-11-11T15:00:00Z", finished_at: "2026-11-11T15:07:00Z" }),
          run({ id: "manual", subject: "manual:manual", issue_number: 118, trigger: "manual", status: "success", dispatched_at: "2026-11-11T15:45:00Z", finished_at: "2026-11-11T15:50:00Z" }),
        ],
      }),
    );
    expect(d.blocked).toEqual([]);
  });

  it("stuck-in-progress is not raised while a run is in flight on the issue", () => {
    const d = buildDashboard(input({ runs: [run({ id: "x", subject: "issue:107", status: "started" })], openIssues: [issue(107, ["feature", "status:in-progress"])] }));
    expect(d.blocked).toEqual([]);
  });

  it("backpressure and quota gates", () => {
    const prs = [1, 2, 3].map((n) => pr(n, `feat/${n}-x`));
    const d = buildDashboard(input({ openPRs: prs, openIssues: [issue(7, ["feature", "status:backlog", "risk:low"])] }));
    expect(d.blocked.map((b) => b.kind)).toEqual(["backpressure"]);
    expect(d.next.queue.find((q) => q.agent === "builder")!.gate).toBe("backpressure");

    const q = buildDashboard(
      input({
        runs: [run({ id: "t", agent: "reviewer", subject: "pr:1", status: "success", finished_at: "2026-11-12T10:00:00Z", model_calls: 350 })],
        openIssues: [issue(7, ["feature", "status:backlog", "risk:low"])],
      }),
    );
    expect(q.today.remaining).toBe(50);
    expect(q.blocked.map((b) => b.kind)).toEqual(["quota"]);
    expect(q.next.queue.find((x) => x.agent === "builder")!.gate).toBe("quota");
  });

  it("scheduled: dependabot cadence and the daily window", () => {
    const d = buildDashboard(
      input({
        openPRs: [pr(88, "dependabot/a", { author: "dependabot[bot]" }), pr(112, "dependabot/b", { author: "dependabot[bot]" })],
        runs: [run({ id: "dep", agent: "reviewer", subject: "dependabot", status: "success", dispatched_at: "2026-11-12T13:00:00Z", finished_at: "2026-11-12T13:05:00Z" })],
      }),
    );
    const dep = d.next.scheduled.find((s) => s.subject === "dependabot")!;
    expect(dep.at).toBe("2026-11-12T14:00:00.000Z");
    expect(dep.note).toContain("#88");
    expect(d.next.scheduled.find((s) => s.agent === "security-triage")!.at).toBe("2026-11-13T06:00:00.000Z");
    const morning = buildDashboard(input({ now: new Date("2026-11-12T06:03:00Z") }));
    expect(morning.next.scheduled.find((s) => s.agent === "incident-responder")!.at).toBe("2026-11-12T06:15:00.000Z");
    const ran = buildDashboard(input({ now: new Date("2026-11-12T06:03:00Z"), runs: [run({ id: "ir", agent: "incident-responder", subject: "2026-11-12", status: "success", dispatched_at: "2026-11-12T06:00:30Z" })] }));
    expect(ran.next.scheduled.find((s) => s.agent === "incident-responder")!.at).toBe("2026-11-13T06:00:00.000Z");
  });

  it("keeps the ledger half when GitHub is down", () => {
    const d = buildDashboard(input({ openIssues: null, openPRs: null, updatedSince: null, githubError: "installation lookup failed: 401", runs: [run({ id: "x", status: "started" })] }));
    expect(d.github_error).toBe("installation lookup failed: 401");
    expect(d.in_flight).toHaveLength(1);
    expect(d.next.queue.every((q) => q.items.length === 0)).toBe(true);
  });

  it("queue order matches what planTick dispatches", () => {
    const openIssues = [issue(2, ["feature", "status:backlog", "risk:low", "priority:3"]), issue(9, ["feature", "status:backlog", "risk:low", "priority:1"]), issue(4, ["feature", "status:backlog"])];
    const d = buildDashboard(input({ openIssues }));
    const first = d.next.queue.find((q) => q.agent === "builder")!.items.find((i) => i.eligible)!;
    const state: TickState = {
      paused: false, quota: { daily: 400, usedToday: 0, reservedInFlight: 0 }, inFlight: [], recentRuns: [], openPRs: [],
      openIssues: openIssues.map((i) => ({ number: i.number, labels: i.labels, authorAssociation: i.authorAssociation, body: i.body })),
      agentSpecs: AGENT_SPECS, maxOpenFactoryPrs: 3, unlabeledIssues: [],
    };
    const dispatched = planTick(state, NOW).find((x) => x.kind === "dispatch") as any;
    expect(dispatched.subject).toBe(`issue:${first.number}`);
    expect(first.number).toBe(9);
    expect(candidateQueue(state, ["feature", "enhancement"]).map((c) => c.issue.number)).toEqual([9, 2, 4]);
  });
});

describe("helpers and html", () => {
  it("clamps the window", () => {
    expect(clampWindow(null)).toBe(48);
    expect(clampWindow("abc")).toBe(48);
    expect(clampWindow("0")).toBe(48);
    expect(clampWindow("24")).toBe(24);
    expect(clampWindow("9999")).toBe(168);
  });
  it("next tick is the next quarter hour", () => {
    expect(nextTickAt(new Date("2026-11-12T13:40:00Z")).toISOString()).toBe("2026-11-12T13:45:00.000Z");
    expect(nextTickAt(new Date("2026-11-12T13:45:00Z")).toISOString()).toBe("2026-11-12T14:00:00.000Z");
  });
  it("renders and escapes", () => {
    const d = buildDashboard(input({ openIssues: [issue(1, ["feature", "status:backlog"], { title: "<script>alert(1)</script>" })], paused: true }));
    const html = dashboardHtml(d);
    expect(html).toContain("<!doctype html>");
    expect(html).toContain("PAUSED");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>alert");
    expect(html).toContain("awaiting-risk");
    expect(html).toContain('href="?hours=24"');
  });
});
