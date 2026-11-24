import { describe, expect, it } from "vitest";
import {
  AUTO_REFRESH_SECONDS,
  buildDashboard,
  clampWindow,
  dashboardHtml,
  dashboardHtmlLegacy,
  dashboardHtmlNext,
  DEFAULT_UI,
  NEXT_THEMES,
  nextTickAt,
  resolveUi,
  THEMES,
  type DashboardInput,
} from "../src/dashboard";
import type { GhIssue, GhPR } from "../src/github";
import { candidateQueue, planTick, type TickState } from "../src/preflight";
import { AGENT_SPECS } from "../src/config";
import type { RunRow } from "../src/types";

const NOW = new Date("2026-11-12T13:40:00Z");
const REPO = "o/target";

const run = (o: Partial<RunRow>): RunRow => ({
  id: "r", agent: "builder", phase: null, subject: "issue:1", trigger: "cron", issue_number: null, pr_number: null,
  dispatched_at: "2026-11-12T13:30:00Z", started_at: null, finished_at: null, status: "dispatched",
  stop_reason: null, error: null, model_calls: 0, reserved_calls: 0, workflow_run_url: null, ...o,
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

describe("merge-blocked card", () => {
  const blocked = (o: Partial<DashboardInput>) => buildDashboard(input(o)).blocked;
  it("replaces lit-pr, joins blockers, and sorts before lit-pr", () => {
    const b = blocked({
      openPRs: [pr(88, "dependabot/x", { labels: ["autonomy:lit"] }), pr(136, "feat/1-x", { labels: ["autonomy:lit", "merge:blocked"] })],
      mergeBlockers: { 136: ["CI not green", "diff too large: 400 lines > 300 (lockfiles excluded)"] },
    });
    expect(b.map((x) => [x.kind, x.subject, x.needs_human])).toEqual([["merge-blocked", "pr:136", true], ["lit-pr", "pr:88", true]]);
    expect(b[0].reason).toBe("Approved, but the merge rules block it: CI not green; diff too large: 400 lines > 300 (lockfiles excluded).");
    expect(b[0].action).toBe(`Fix the blockers, or merge it by hand if you accept them: gh pr merge 136 -R ${REPO} --squash`);
    expect(b[0].todo).toBe("Read why the merge is blocked on PR #136. Fix it, or merge it yourself if the blockers are acceptable.");
    expect(b[0].url).toBe(`https://github.com/${REPO}/pull/136`);
  });
  it("falls back when blockers are missing, null or empty", () => {
    const fallback = "Approved, but the merge rules block it (see the factory comment on the PR).";
    const prs = [pr(1, "feat/1-a", { labels: ["merge:blocked"] })];
    expect(blocked({ openPRs: prs })[0].reason).toBe(fallback);
    expect(blocked({ openPRs: prs, mergeBlockers: { 1: null } })[0].reason).toBe(fallback);
    expect(blocked({ openPRs: prs, mergeBlockers: { 1: [] } })[0].reason).toBe(fallback);
  });
  it("needs-human wins over merge:blocked", () => {
    const b = blocked({ openPRs: [pr(2, "feat/2-b", { labels: ["merge:blocked", "needs-human"] })] });
    expect(b.map((x) => x.kind)).toEqual(["needs-human-pr"]);
  });
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
        openPRs: [pr(88, "dependabot/npm_and_yarn/vitest", { author: "dependabot[bot]", labels: ["autonomy:lit"] }), pr(99, "fix/9-x", { labels: ["needs-human"] }), pr(98, "feat/4-y", { labels: ["needs-human", "revise:attempted"] })],
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
      ["needs-human-pr", "pr:98", true],
      ["needs-human-pr", "pr:99", true],
      ["bugs-first", "factory", false],
    ]);
    expect(d.blocked.find((b) => b.subject === "pr:98")?.reason).toBe("revise attempted; a human must take over (see the factory comments on the PR)");
    expect(d.blocked.find((b) => b.subject === "pr:99")?.reason).toContain("could not handle");
    expect(d.blocked.find((b) => b.kind === "stuck-in-progress")?.action).toBe(`gh issue edit 50 -R ${REPO} --remove-label status:in-progress --add-label status:backlog`);
    expect(d.blocked.find((b) => b.kind === "bugs-first")?.title).toBe("Open bug #125 at priority 1 or 2");
    // every blocker carries a plain-English step for a human
    expect(d.blocked.every((b) => b.todo.length > 0)).toBe(true);
    expect(d.blocked.find((b) => b.kind === "awaiting-risk")?.todo).toContain("risk:low");
    expect(d.blocked.find((b) => b.kind === "plan-review")?.todo).toBe("Read the plan comment on issue #116. If it looks right, add the label plan:approved.");
    expect(d.blocked.find((b) => b.kind === "lit-pr")?.todo).toContain("approve it");
    const builder = d.next.queue.find((q) => q.agent === "builder")!;
    expect(builder.gate).toBe("bugs_first");
    expect(builder.items.map((i) => [i.number, i.reason])).toEqual([
      [100, "awaiting-risk"], [101, "author-not-trusted"], [102, "blocked-by:#116"], [117, "needs-human"], [130, null],
    ]);
    const bugs = d.next.queue.find((q) => q.agent === "bug-fixer")!;
    expect(bugs.items.map((i) => [i.number, i.eligible])).toEqual([[119, false], [125, true]]);
    expect(bugs.gate).toBeNull();
  });

  it("a failed builder run puts the issue in backoff, then at the cap", () => {
    const openIssues = [issue(107, ["feature", "status:backlog", "risk:low"])];
    const fail = (id: string, at: string) => run({ id, subject: "issue:107", status: "failed", dispatched_at: at });
    const one = buildDashboard(input({ openIssues, runs: [fail("a", "2026-11-12T13:15:00Z")] }));
    const b = one.blocked.find((x) => x.subject === "issue:107" && x.kind === "backoff")!;
    expect(b.needs_human).toBe(false);
    expect(b.reason).toContain("2026-11-12T14:15Z");
    expect(b.action).toBe("Nothing to do unless you want it now: gh workflow run builder.yml -f issue=107");
    const three = buildDashboard(input({
      openIssues,
      runs: [fail("a", "2026-11-12T11:00:00Z"), fail("b", "2026-11-12T12:00:00Z"), fail("c", "2026-11-12T13:15:00Z")],
    }));
    const cap = three.blocked.find((x) => x.kind === "failure-cap")!;
    expect(cap.needs_human).toBe(true);
    expect(cap.reason).toContain("2026-11-13T11:00Z");
    expect(cap.action).toBe("gh workflow run builder.yml -f issue=107");
    expect(three.next.queue.find((q) => q.agent === "builder")!.items[0].reason).toBe("failure-cap:2026-11-13T11:00Z");
  });

  describe("failed-run card", () => {
    const failed = (o: Partial<RunRow> = {}) =>
      run({ id: "f1", agent: "bug-fixer", subject: "issue:121", issue_number: 121, status: "failed", stop_reason: "end_turn",
        error: "apply: no commits ahead of main; blocked: edit_file: package.json is protected by the target profile (package.json)",
        dispatched_at: "2026-11-12T13:00:00Z", finished_at: "2026-11-12T13:01:25Z", ...o });
    const card = (runs: RunRow[]) => buildDashboard(input({ runs })).blocked.find((b) => b.kind === "failed-run")!;

    it("says what failed and when the tick retries", () => {
      const c = card([failed()]);
      expect(c.reason).toBe(
        "Last run failed at 2026-11-12T13:01:25Z: apply: no commits ahead of main; blocked: edit_file: package.json is protected by the target profile (package.json). Retries automatically after 14:00 UTC.",
      );
      expect(c.detail).toContain("package.json is protected");
    });

    it("guard text as the action when the error contains blocked:", () => {
      expect(card([failed()]).action).toMatch(/^A guard blocked it, so a retry fails the same way/);
      expect(card([failed({ error: "boom" })]).action).toMatch(/^Read the run log/);
    });

    it("says the next tick retries an issue once its backoff is over", () => {
      const c = card([failed({ dispatched_at: "2026-11-12T10:00:00Z", finished_at: "2026-11-12T10:01:25Z" })]);
      expect(c.reason).toMatch(/: apply: .*\. The backoff is over, so the next tick retries it\.$/);
      expect(c.reason).not.toContain("Nothing will retry it");
    });

    it("failure cap sentence", () => {
      const runs = [0, 1, 2].map((n) => failed({ id: `f${n}`, dispatched_at: `2026-11-12T0${n + 6}:00:00Z`, finished_at: `2026-11-12T0${n + 6}:05:00Z` }));
      expect(card(runs).reason).toContain("Failure cap: no retry until 2026-11-13 06:00 UTC.");
    });

    it("falls back to stop_reason, then status alone, and says nothing retries non-issue subjects", () => {
      const c = card([failed({ error: null })]);
      expect(c.reason).toContain("failed at 2026-11-12T13:01:25Z: end_turn.");
      expect(c.detail).toBeNull();
      const d = card([failed({ error: null, stop_reason: null, subject: "deploy:1", issue_number: null })]);
      expect(d.reason).toBe("Last run failed at 2026-11-12T13:01:25Z. Nothing will retry it.");
    });
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
    expect(candidateQueue(state, ["feature", "enhancement"], NOW).map((c) => c.issue.number)).toEqual([9, 2, 4]);
  });

  it("revise candidates lead the queue row and match the dispatch", () => {
    const openIssues = [issue(2, ["feature", "status:backlog", "risk:low"])];
    const bot = "software-factory-agent-bot[bot]";
    const openPRs = [pr(40, "feat/7-thing", { author: bot, labels: ["revise:requested"], title: "Thing" }), pr(41, "fix/9-x", { author: bot, labels: ["revise:requested"] }), pr(42, "feat/12-h", { author: "alice", labels: ["revise:requested"] })];
    const runs = [run({ id: "x", agent: "bug-fixer", subject: "revise:9", status: "failed", dispatched_at: "2026-11-12T13:00:00Z", finished_at: "2026-11-12T13:10:00Z" })];
    const d = buildDashboard(input({ openIssues, openPRs, runs }));
    const b = d.next.queue.find((q) => q.agent === "builder")!.items;
    expect(b[0]).toMatchObject({ number: 40, title: "Thing", eligible: true, reason: "revise", url: `https://github.com/${REPO}/pull/40` });
    expect(b.map((i) => i.number)).toEqual([40, 2]);
    const f = d.next.queue.find((q) => q.agent === "bug-fixer")!.items;
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ number: 41, eligible: false });
    expect(f[0].reason).toMatch(/^retry-after:/);
    const state: TickState = {
      paused: false, quota: { daily: 400, usedToday: 0, reservedInFlight: 0 }, inFlight: [], recentRuns: [], openPRs: openPRs.map((p) => ({ number: p.number, headRef: p.headRef, body: p.body, author: p.author, labels: p.labels })),
      openIssues: openIssues.map((i) => ({ number: i.number, labels: i.labels, authorAssociation: i.authorAssociation, body: i.body })),
      agentSpecs: AGENT_SPECS, maxOpenFactoryPrs: 3, unlabeledIssues: [],
    };
    const first = planTick(state, NOW).find((x) => x.kind === "dispatch" && x.agent === "builder") as any;
    expect(first.subject).toBe("revise:7");
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
  it("resolveUi defaults to old and accepts old/new", () => {
    expect(DEFAULT_UI).toBe("old");
    expect(resolveUi(null)).toBe("old");
    expect(resolveUi("new")).toBe("new");
    expect(resolveUi("old")).toBe("old");
    expect(resolveUi("bogus")).toBe("old");
  });
  it("renders and escapes in both Old and New UIs", () => {
    const d = buildDashboard(input({ openIssues: [issue(1, ["feature", "status:backlog"], { title: "<script>alert(1)</script>" })], paused: true }));
    for (const [ui, expectedWin, expectedToggle] of [
      [undefined, 'href="?hours=24&ui=old"', 'href="?hours=48&ui=new"'],
      ["new", 'href="?hours=24&ui=new"', 'href="?hours=48&ui=old"'],
    ] as const) {
      const html = dashboardHtml(d, ui);
      expect(html).toContain("<!doctype html>");
      expect(html).toContain("PAUSED");
      expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
      expect(html).not.toContain("<script>alert");
      expect(html).toContain("awaiting-risk");
      expect(html).toContain("<b>To do:</b> Size issue #1: add the label risk:low");
      expect(html).toContain("you: add risk:low or risk:high");
      expect(html).toContain(expectedWin);
      expect(html).toContain(expectedToggle);
    }
  });
});

function contrastRatio(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  return (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
}

describe("html (legacy UI — default): refresh, theme and panels", () => {
  const doc = buildDashboard(
    input({
      openIssues: [issue(1, ["feature", "status:backlog"], { title: "A very long title ".repeat(6) }), issue(2, ["feature", "status:backlog"])],
      // overdue-run (waits) sorts before awaiting-risk (you) by severity; the page lists the human card first
      runs: [run({ id: "stale", agent: "triage", subject: "issue:3", status: "started", dispatched_at: "2026-11-12T12:00:00Z" })],
    }),
  );
  const html = dashboardHtml(doc);
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);

  it("defaults to legacy UI when second argument is omitted", () => {
    expect(html).toBe(dashboardHtmlLegacy(doc));
  });

  it("auto-refresh: no meta refresh, a hidden-until-JS toggle, 60 s", () => {
    expect(AUTO_REFRESH_SECONDS).toBe(60);
    expect(html).not.toContain("http-equiv");
    expect(html).toContain('<div class="ctl" id="ctl" hidden>');
    expect(html).toContain('<input type="checkbox" id="ar"> auto-refresh (60 s)');
    expect(html).toContain('<span id="arc" class="dim"></span>');
    expect(html).not.toContain("Refreshes every 5 minutes");
    expect(html).toContain("reloads every 60 s while auto-refresh is ticked");
    expect(html).toContain('<a href="?hours=24&ui=old">');
    expect(html).toContain('id="ui-toggle" href="?hours=48&ui=new"');
  });

  it("theme: light / dark / system select, data-theme overrides on the tokens, set before paint", () => {
    expect(html).toContain('<select id="theme" aria-label="Colour theme"><option value="system">system</option><option value="light">light</option><option value="dark">dark</option></select>');
    expect(html).toContain('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19');
    expect(html).toContain(':root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19');
    expect(html.indexOf("factory-dash:theme")).toBeLessThan(html.indexOf("<style>"));
  });

  it("boot script persists ?ui=old and redirects bare /dashboard to ?ui=new when stored", () => {
    const boot = scripts[0];
    expect(boot).toContain("factory-dash:theme");
    expect(boot).toContain("factory-dash:ui");
    expect(boot).toContain('p.set("ui","new")');
    expect(boot).toContain("location.replace");
    const runBoot = (search: string, stored: Record<string, string> = {}) => {
      const attrs: Record<string, string> = {};
      let replaced: string | null = null;
      const d = { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; } } };
      const loc = { search, hash: "#p-next", replace: (url: string) => { replaced = url; } };
      const ls = {
        getItem: (k: string) => (k in stored ? stored[k] : null),
        setItem: (k: string, v: string) => { stored[k] = v; },
      };
      new Function("document", "location", "localStorage", boot)(d, loc, ls);
      return { attrs, stored, replaced };
    };
    expect(runBoot("?hours=24&ui=old")).toMatchObject({ stored: { "factory-dash:ui": "old" }, replaced: null });
    expect(runBoot("?hours=24", { "factory-dash:ui": "new" })).toMatchObject({ replaced: "?hours=24&ui=new#p-next" });
    expect(runBoot("", { "factory-dash:ui": "new" })).toMatchObject({ replaced: "?ui=new#p-next" });
    expect(runBoot("?hours=24", { "factory-dash:ui": "old" })).toMatchObject({ replaced: null });
    expect(runBoot("", { "factory-dash:theme": "dark" })).toMatchObject({ attrs: { "data-theme": "dark" }, replaced: null });
  });

  it("inline only: no external scripts or stylesheets, and the scripts parse", () => {
    expect(html).not.toMatch(/<script[^>]*\ssrc=|<link[^>]*stylesheet/);
    expect(scripts).toHaveLength(2);
    for (const body of scripts) expect(() => new Function(body)).not.toThrow();
    for (const k of ["localStorage", "sessionStorage"]) {
      // every storage access sits inside a try block
      const body = scripts.join("\n");
      expect(body.split(k).length - 1).toBe((body.match(new RegExp(`try\\{[^}]*${k}`, "g")) ?? []).length);
    }
  });

  it("panels: native details with counts, Blocked and In flight open, Happened sub-tables collapsed", () => {
    const open = (id: string) => new RegExp(`<details class="panel p\\d" id="${id}" open>`).test(html);
    const exists = (id: string) => html.includes(`id="${id}"`);
    for (const id of ["p-blocked", "p-flight", "p-next", "p-happened", "p-scheduled"]) expect([id, open(id)]).toEqual([id, true]);
    for (const id of ["p-movement", "p-calls", "p-counts", "p-runs"]) expect([id, exists(id), open(id)]).toEqual([id, true, false]);
    expect(html).toContain('<summary><h2>In flight <span class="count">(1)</span></h2></summary>');
    expect(html).toContain('<summary><h3>Runs <span class="count">(1)</span></h3></summary>');
    expect(html).toContain('<details id="p-decisions">');
    expect(html).toContain('<button type="button" id="expand">expand all</button>');
    expect(html).toContain('<nav aria-label="Sections"><a href="#p-blocked">Blocked</a>');
  });

  it("needs-human cards come before waiting ones; long titles truncate with the full text on hover", () => {
    expect(html.indexOf("</span> awaiting-risk")).toBeGreaterThan(0);
    expect(html.indexOf("</span> awaiting-risk")).toBeLessThan(html.indexOf("</span> overdue-run"));
    const t = "A very long title ".repeat(6);
    expect(html).toContain(`<span class="trunc" title="${t}">${t}</span>`);
    expect(html).toMatch(/<time datetime="2026-11-12T12:00:00Z">11-12 12:00<\/time> <span class="dim">\(1 h 40 min ago\)<\/span>/);
  });

  it("text and status colours pass WCAG AA on the background and cards in both themes", () => {
    for (const [name, t] of Object.entries(THEMES))
      for (const fg of ["fg", "fg2", "accent", "good", "warn", "bad", "mute"] as const)
        for (const bg of ["bg", "card"] as const) expect([name, fg, bg, contrastRatio(t[fg], t[bg]) >= 4.5]).toEqual([name, fg, bg, true]);
  });
});

describe("html (new UI — ?ui=new)", () => {
  const doc = buildDashboard(
    input({
      openIssues: [issue(1, ["feature", "status:backlog"], { title: "A very long title ".repeat(6) }), issue(2, ["feature", "status:backlog"])],
      runs: [
        run({ id: "stale", agent: "triage", subject: "issue:3", status: "started", dispatched_at: "2026-11-12T12:00:00Z" }),
        run({ id: "bad", agent: "builder", subject: "issue:4", status: "failed", dispatched_at: "2026-11-12T11:00:00Z", finished_at: "2026-11-12T11:10:00Z", model_calls: 12 }),
      ],
    }),
  );
  const html = dashboardHtml(doc, "new");
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);

  it("matches dashboardHtmlNext when ui is new", () => {
    expect(html).toBe(dashboardHtmlNext(doc));
  });

  it("inline only: no external scripts or stylesheets, 2 valid scripts, safe storage access", () => {
    expect(html).not.toMatch(/<script[^>]*\ssrc=|<link[^>]*stylesheet/);
    expect(scripts).toHaveLength(2);
    for (const body of scripts) expect(() => new Function(body)).not.toThrow();
    const body = scripts.join("\n");
    for (const k of ["localStorage", "sessionStorage"]) {
      const total = body.split(k).length - 1;
      const guarded = (body.match(new RegExp(`try\\{[^}]*${k}`, "g")) ?? []).length;
      expect([k, total]).toEqual([k, guarded]);
    }
  });

  it("boot script runs before <style> and handles theme, density, and ui storage keys", () => {
    const styleIdx = html.indexOf("<style>");
    expect(styleIdx).toBeGreaterThan(0);
    const boot = scripts[0];
    expect(html.indexOf(boot)).toBeLessThan(styleIdx);
    expect(boot).toContain("factory-dash:theme");
    expect(boot).toContain("factory-dash:density");
    expect(boot).toContain("factory-dash:ui");
    expect(boot).not.toContain("location.replace");
    expect(html.indexOf("factory-dash:")).toBeLessThan(styleIdx);
    const stored: Record<string, string> = {};
    const d = { documentElement: { setAttribute: () => {} } };
    const ls = { getItem: (k: string) => stored[k] ?? null, setItem: (k: string, v: string) => { stored[k] = v; } };
    new Function("document", "location", "localStorage", boot)(d, { search: "?ui=new" }, ls);
    expect(stored["factory-dash:ui"]).toBe("new");
  });

  it("controls: theme, density, auto-refresh, expand/collapse, and always-visible UI toggle", () => {
    expect(html).toContain('<div class="ctl" id="ctl" hidden>');
    expect(html).toContain('<select id="theme" aria-label="Colour theme"><option value="system">system</option><option value="light">light</option><option value="dark">dark</option></select>');
    expect(html).toContain('<select id="density" aria-label="Layout density"><option value="comfortable">comfortable</option><option value="compact">compact</option></select>');
    expect(html).toContain('<input type="checkbox" id="ar"> auto-refresh (60 s)');
    expect(html).toContain('<button type="button" id="expand">Expand all</button>');
    expect(html).toContain('<button type="button" id="collapse">Collapse all</button>');
    expect(html).toContain('id="ui-toggle" href="?hours=48&ui=old"');
  });

  it("panels: native details with expected open/collapsed states", () => {
    const open = (id: string) => new RegExp(`<details class="panel p\\d" id="${id}" open>`).test(html);
    const exists = (id: string) => html.includes(`id="${id}"`);
    for (const id of ["p-blocked", "p-flight", "p-next", "p-happened", "p-scheduled"]) expect([id, open(id)]).toEqual([id, true]);
    for (const id of ["p-movement", "p-calls", "p-counts", "p-runs"]) expect([id, exists(id), open(id)]).toEqual([id, true, false]);
    expect(html).toContain('<details id="p-decisions">');
  });

  it("enhancements: triage filter chips, copy buttons, SVG calls chart, runs filters, action callout, and human-first order", () => {
    expect(html).toContain('data-filter="all"');
    expect(html).toContain('data-filter="human"');
    expect(html).toContain('data-filter="waits"');
    expect(html).toContain('class="copy-btn" data-copy="');
    expect(html).toContain('class="calls-svg"');
    expect(html).toContain('data-run-filter="failed"');
    expect(html).toContain('<div class="callout">');
    expect(html.indexOf('<span class="blk-kind">awaiting-risk</span>')).toBeGreaterThan(0);
    expect(html.indexOf('<span class="blk-kind">awaiting-risk</span>')).toBeLessThan(html.indexOf('<span class="blk-kind">overdue-run</span>'));
  });

  it("NEXT_THEMES pass WCAG AA (>= 4.5:1) on bg/card/elev and tinted status badges", () => {
    for (const [name, t] of Object.entries(NEXT_THEMES)) {
      for (const fg of ["fg", "fg2", "accent", "good", "warn", "bad", "mute"] as const) {
        for (const bg of ["bg", "card", "elev"] as const) {
          expect([name, fg, bg, contrastRatio(t[fg], t[bg]) >= 4.5]).toEqual([name, fg, bg, true]);
        }
      }
      for (const [fg, bg] of [
        ["good", "goodBg"],
        ["warn", "warnBg"],
        ["bad", "badBg"],
        ["mute", "muteBg"],
        ["accent", "accentBg"],
      ] as const) {
        expect([name, fg, bg, contrastRatio(t[fg], t[bg]) >= 4.5]).toEqual([name, fg, bg, true]);
      }
    }
  });
});

describe("costs", () => {
  const runs = [
    run({ id: "a", agent: "builder", status: "success", dispatched_at: "2026-11-12T09:00:00Z", finished_at: "2026-11-12T09:30:00Z", paid_cost_usd: 0.12345 }),
    run({ id: "b", agent: "reviewer", status: "success", dispatched_at: "2026-11-12T10:00:00Z", finished_at: "2026-11-12T10:05:00Z", paid_cost_usd: 0 }),
    run({ id: "c", agent: "reviewer", status: "failed", dispatched_at: "2026-11-11T10:00:00Z", finished_at: "2026-11-11T10:05:00Z", paid_cost_usd: 0.05 }),
    run({ id: "d", agent: "builder", status: "started", dispatched_at: "2026-11-12T13:30:00Z", paid_reserved_usd: 0.3 }),
  ];
  const paid = { cap: 0.5, runCap: 0.3, blocked: false };

  it("sums today's spend and reservations against the cap, by day and by agent", () => {
    const c = buildDashboard(input({ runs, paid })).costs;
    expect(c.today).toMatchObject({ spent_usd: 0.1235, reserved_usd: 0.3, cap_usd: 0.5, remaining_usd: 0.0766, run_cap_usd: 0.3, blocked: false, exhausted: false });
    expect(c.today.resets_at).toBe("2026-11-13T00:00:00.000Z");
    expect(c.window_usd).toBe(0.1735);
    expect(c.by_day.find((x) => x.day === "2026-11-11")?.usd).toBe(0.05);
    expect(c.by_day.find((x) => x.day === "2026-11-12")?.usd).toBe(0.1235);
    expect(c.by_agent).toEqual([
      { agent: "builder", usd: 0.1235, runs: 2, paid_runs: 1 },
      { agent: "reviewer", usd: 0.05, runs: 2, paid_runs: 1 },
    ]);
    expect(buildDashboard(input({ runs, paid })).happened.runs.find((r) => r.run_id === "a")?.paid_cost_usd).toBe(0.1235);
  });

  it("without a paid key the cap is 0 and nothing is blocked", () => {
    const d = buildDashboard(input({ runs: [] }));
    expect(d.costs.today).toMatchObject({ spent_usd: 0, cap_usd: 0, blocked: false, exhausted: false });
    expect(d.blocked.some((b) => b.kind.startsWith("paid-"))).toBe(false);
  });

  it("shows a human card when the paid key is blocked and a waiting card when the budget is spent", () => {
    const spent = [run({ id: "x", status: "failed", stop_reason: "paid_budget", dispatched_at: "2026-11-12T12:00:00Z", finished_at: "2026-11-12T12:10:00Z", paid_cost_usd: 0.5 })];
    const d = buildDashboard(input({ runs: spent, paid: { ...paid, blocked: true } }));
    const kinds = d.blocked.map((b) => [b.kind, b.needs_human]);
    expect(kinds).toContainEqual(["paid-blocked", true]);
    expect(kinds).toContainEqual(["paid-budget", false]);
    expect(d.blocked.find((b) => b.kind === "paid-budget")?.title).toBe("Paid budget spent: $0.50 of $0.50 today");
  });

  it("renders the tile, panel and run column in both UIs", () => {
    const d = buildDashboard(input({ runs, paid }));
    for (const html of [dashboardHtmlLegacy(d), dashboardHtmlNext(d)]) {
      expect(html).toContain("Paid spend today");
      expect(html).toContain("Paid spend: $0.17 in 48 h");
      expect(html).toContain('id="p-costs"');
      expect(html).toContain("$0.12");
      expect(html).toContain(">paid<");
    }
    expect(dashboardHtmlNext(d)).toContain('aria-label="Estimated paid spend per UTC day"');
    expect(dashboardHtmlLegacy(buildDashboard(input()))).toContain("no paid key");
  });
});
