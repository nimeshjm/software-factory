import { afterEach, describe, expect, it, vi } from "vitest";
import { DEBOUNCE_MS, SECURITY_DEBOUNCE_MS, debounceWindowMs, hmacHex, isDebounced, isDroppedSender, handleWebhook, mapEvent, prClosedEvent, verifySignature } from "../src/webhooks";

const T = "acme/widgets";
const DEPLOY = { workflowName: "Ship it", branch: "trunk" };
describe("webhooks", () => {
  it("verifies signature", async () => {
    const sig = "sha256=" + (await hmacHex("s3cret", "{}"));
    expect(await verifySignature("s3cret", "{}", sig)).toBe(true);
    expect(await verifySignature("s3cret", "{}x", sig)).toBe(false);
    expect(await verifySignature("other", "{}", sig)).toBe(false);
    expect(await verifySignature("s3cret", "{}", null)).toBe(false);
    expect(await verifySignature("s3cret", "{}", "sha1=abc")).toBe(false);
  });
  it("drops bot senders except check_suite/workflow_run", () => {
    const p = { sender: { login: "dependabot[bot]" } };
    expect(isDroppedSender("issues", p)).toBe(true);
    expect(isDroppedSender("pull_request", p)).toBe(true);
    expect(isDroppedSender("check_suite", p)).toBe(false);
    expect(isDroppedSender("workflow_run", p)).toBe(false);
    expect(isDroppedSender("issues", { sender: { login: "octocat" } })).toBe(false);
  });
  it("exempts security alert events from the bot-sender drop", () => {
    expect(isDroppedSender("code_scanning_alert", { sender: { login: "github[bot]" } })).toBe(false);
    expect(isDroppedSender("dependabot_alert", { sender: { login: "dependabot[bot]" } })).toBe(false);
  });
  it("maps security alert events", () => {
    const repository = { full_name: T };
    const want = [{ agent: "security-triage", subject: "security", phase: "triage", issue: "", pr: "" }];
    expect(mapEvent("code_scanning_alert", { action: "created", alert: { number: 1 }, repository }, T, DEPLOY)).toEqual(want);
    expect(mapEvent("code_scanning_alert", { action: "appeared_in_branch", alert: { number: 1 }, repository }, T, DEPLOY)).toEqual(want);
    expect(mapEvent("dependabot_alert", { action: "created", alert: { number: 2 }, repository }, T, DEPLOY)).toEqual(want);
    expect(mapEvent("code_scanning_alert", { action: "fixed", alert: { number: 1 }, repository }, T, DEPLOY)).toEqual([]);
    expect(mapEvent("code_scanning_alert", { action: "closed_by_user", repository }, T, DEPLOY)).toEqual([]);
    expect(mapEvent("dependabot_alert", { action: "dismissed", alert: { number: 2 }, repository }, T, DEPLOY)).toEqual([]);
    expect(mapEvent("dependabot_alert", { action: "appeared_in_branch", repository }, T, DEPLOY)).toEqual([]);
    expect(mapEvent("dependabot_alert", { action: "created", repository: { full_name: "x/y" } }, T, DEPLOY)).toEqual([]);
  });
  it("maps events", () => {
    const repository = { full_name: T };
    expect(mapEvent("issues", { action: "opened", issue: { number: 4 }, repository }, T, DEPLOY)).toEqual([
      { agent: "triage", subject: "issue:4", phase: "triage", issue: "4", pr: "" }]);
    expect(mapEvent("pull_request", { action: "synchronize", pull_request: { number: 9 }, repository }, T, DEPLOY)[0].subject).toBe("pr:9");
    expect(mapEvent("pull_request", { action: "closed", pull_request: { number: 9 }, repository }, T, DEPLOY)).toEqual([]);
    expect(mapEvent("check_suite", { action: "completed", check_suite: { pull_requests: [{ number: 3 }] }, repository }, T, DEPLOY)[0].subject).toBe("pr:3");
    expect(mapEvent("check_suite", { action: "completed", check_suite: { pull_requests: [] }, repository }, T, DEPLOY)).toEqual([]);
    const wr = (o: object) => ({ action: "completed", repository, workflow_run: { name: DEPLOY.workflowName, head_branch: DEPLOY.branch, conclusion: "success", head_sha: "abc", ...o } });
    expect(mapEvent("workflow_run", wr({}), T, DEPLOY)[0]).toMatchObject({ agent: "verifier", subject: "deploy:abc" });
    expect(mapEvent("workflow_run", wr({ conclusion: "failure" }), T, DEPLOY)).toEqual([]);
    expect(mapEvent("workflow_run", wr({ name: "CI" }), T, DEPLOY)).toEqual([]);
    expect(mapEvent("workflow_run", wr({ head_branch: "dev" }), T, DEPLOY)).toEqual([]);
    expect(mapEvent("issues", { action: "opened", issue: { number: 4 }, repository: { full_name: "x/y" } }, T, DEPLOY)).toEqual([]);
  });
  it("maps a submitted human approval to the reviewer, and nothing else from reviews", () => {
    const repository = { full_name: T };
    const review = (state: string, action = "submitted") => ({ action, repository, pull_request: { number: 126 }, review: { state }, sender: { login: "alice" } });
    expect(mapEvent("pull_request_review", review("approved"), T, DEPLOY)).toEqual([
      { agent: "reviewer", subject: "pr:126", phase: "review", issue: "", pr: "126" }]);
    expect(mapEvent("pull_request_review", review("APPROVED"), T, DEPLOY)[0].subject).toBe("pr:126");
    expect(mapEvent("pull_request_review", review("commented"), T, DEPLOY)).toEqual([]);
    expect(mapEvent("pull_request_review", review("changes_requested"), T, DEPLOY)).toEqual([]);
    expect(mapEvent("pull_request_review", review("approved", "dismissed"), T, DEPLOY)).toEqual([]);
    expect(mapEvent("pull_request_review", review("approved", "edited"), T, DEPLOY)).toEqual([]);
    expect(mapEvent("pull_request_review", { action: "submitted", repository, review: { state: "approved" } }, T, DEPLOY)).toEqual([]);
    // A bot's approval is dropped before mapping, like any other bot-sent event.
    expect(isDroppedSender("pull_request_review", { sender: { login: "software-factory-agent-bot[bot]" } })).toBe(true);
    expect(isDroppedSender("pull_request_review", { sender: { login: "alice" } })).toBe(false);
  });
  it("debounces an approval only against an in-flight run", () => {
    expect(debounceWindowMs("reviewer", "pull_request")).toBe(DEBOUNCE_MS);
    expect(debounceWindowMs("reviewer", "check_suite")).toBe(DEBOUNCE_MS);
    expect(debounceWindowMs("reviewer", "pull_request_review")).toBe(0);
    expect(debounceWindowMs("security-triage", "dependabot_alert")).toBe(SECURITY_DEBOUNCE_MS);
    const now = new Date("2026-10-21T10:00:00Z");
    const w = debounceWindowMs("reviewer", "pull_request_review");
    expect(isDebounced([{ status: "started", finished_at: null }], now, w)).toBe(true);
    expect(isDebounced([{ status: "dispatched", finished_at: null }], now, w)).toBe(true);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T09:59:30Z" }], now, w)).toBe(false);
  });
  it("verifier trigger follows the configured workflow name and branch", () => {
    const repository = { full_name: T };
    const run = (name: string, head_branch: string) => ({
      action: "completed",
      repository,
      workflow_run: { name, head_branch, conclusion: "success", head_sha: "def" },
    });
    const other = { workflowName: "Release", branch: "production" };
    expect(mapEvent("workflow_run", run("Release", "production"), T, other)[0]).toMatchObject({ agent: "verifier", subject: "deploy:def" });
    expect(mapEvent("workflow_run", run("Ship it", "trunk"), T, other)).toEqual([]);
    expect(mapEvent("workflow_run", run("Release", "trunk"), T, other)).toEqual([]);
    expect(mapEvent("workflow_run", run("Ship it", "production"), T, other)).toEqual([]);
    expect(mapEvent("workflow_run", run("Deploy", "main"), T, DEPLOY)).toEqual([]);
  });
  it("debounces reviewer", () => {
    const now = new Date("2026-10-21T10:00:00Z");
    expect(isDebounced([{ status: "started", finished_at: null }], now)).toBe(true);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T09:57:00Z" }], now)).toBe(true);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T09:50:00Z" }], now)).toBe(false);
    expect(isDebounced([], now)).toBe(false);
  });
  it("debounces security-triage to one run per 60 minutes", () => {
    const now = new Date("2026-10-21T10:00:00Z");
    const w = SECURITY_DEBOUNCE_MS;
    expect(w).toBe(3_600_000);
    expect(isDebounced([{ status: "started", finished_at: null }], now, w)).toBe(true);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T09:30:00Z" }], now, w)).toBe(true);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T08:59:00Z" }], now, w)).toBe(false);
    expect(isDebounced([{ status: "success", finished_at: "2026-10-21T09:30:00Z" }], now)).toBe(false);
  });
});

describe("prClosedEvent", () => {
  const pr = (o: Record<string, unknown> = {}, labels: string[] = []) => ({
    action: "closed",
    pull_request: {
      number: 5, user: { login: "factory-app[bot]" }, head: { ref: "feat/12-add-x" }, merged: true, merged_by: { login: "alice" },
      created_at: "2026-11-20T10:00:00Z", closed_at: "2026-11-20T11:01:05Z", labels: labels.map((name) => ({ name })), ...o,
    },
  });
  it("merged first pass on a dark PR", () => {
    expect(prClosedEvent(pr({}, ["autonomy:dark"]))).toEqual({
      name: "pr.closed", pr_number: 5, issue_number: 12, agent: "builder", merged: true, outcome: "merged_first_pass", revised: false,
      needs_human: false, autonomy: "dark", author: "factory-app[bot]", merged_by: "alice", open_s: 3665,
    });
  });
  it("merged after revise", () => {
    expect(prClosedEvent(pr({}, ["revise:attempted", "autonomy:lit"]))).toMatchObject({ outcome: "merged_after_revise", revised: true, autonomy: "lit" });
  });
  it("closed unmerged with needs-human", () => {
    expect(prClosedEvent(pr({ merged: false, merged_by: null }, ["needs-human"]))).toMatchObject({
      merged: false, outcome: "closed_unmerged", needs_human: true, autonomy: null, merged_by: null,
    });
  });
  it("maps fix/ to bug-fixer", () => {
    expect(prClosedEvent(pr({ head: { ref: "fix/3-bug" } }))).toMatchObject({ agent: "bug-fixer", issue_number: 3 });
  });
  it("returns null for dependabot and human PRs", () => {
    expect(prClosedEvent(pr({ user: { login: "dependabot[bot]" }, head: { ref: "dependabot/npm_and_yarn/x" } }))).toBeNull();
    expect(prClosedEvent(pr({ user: { login: "alice" } }))).toBeNull();
  });
});

describe("handleWebhook pr.closed", () => {
  afterEach(() => vi.unstubAllGlobals());
  const deliver = async (repo: string) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const env = {
      GITHUB_WEBHOOK_SECRET: "s", HONEYCOMB_API_KEY: "k", TARGET_REPO: "o/r", DEPLOY_WORKFLOW_NAME: "d", TARGET_DEFAULT_BRANCH: "main",
      DB: { prepare: () => ({ bind: () => ({ run: async () => ({ meta: { changes: 1 } }) }) }) },
    } as never;
    const body = JSON.stringify({
      action: "closed", repository: { full_name: repo }, sender: { login: "factory-app[bot]" },
      pull_request: { number: 5, user: { login: "factory-app[bot]" }, head: { ref: "fix/3-x" }, merged: true, created_at: "2026-11-20T10:00:00Z", closed_at: "2026-11-20T10:00:10Z", labels: [] },
    });
    const req = new Request("https://x/webhook", {
      method: "POST", body,
      headers: { "x-github-event": "pull_request", "x-github-delivery": "d1", "x-hub-signature-256": `sha256=${await hmacHex("s", body)}` },
    });
    const res = await handleWebhook(req, env, { waitUntil() {} } as never);
    expect(await res.json()).toEqual({ ignored: "bot sender" });
    return fetchMock;
  };
  it("emits the event for a bot-sender closed delivery and dispatches nothing", async () => {
    const fetchMock = await deliver("O/R");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ name: "pr.closed", agent: "bug-fixer", outcome: "merged_first_pass" });
  });
  it("emits nothing for a factory-shaped PR from another repository", async () => {
    const fetchMock = await deliver("other/repo");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
