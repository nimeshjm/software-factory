import { describe, expect, it } from "vitest";
import { hmacHex, isDebounced, isDroppedSender, mapEvent, verifySignature } from "../src/webhooks";

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
});
