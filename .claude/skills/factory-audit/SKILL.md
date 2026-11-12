---
name: factory-audit
description: Audit what the software factory did over the last day (or any window) and verify progress against HANDOFF.md - workflow runs and their artifacts, control plane status, target issue and PR movement, failures, stalls and plan drift. Read-only. Use for "what has the factory been doing", "is it stuck", a daily or post-weekend check, or before writing the next HANDOFF.md "Do next".
---

# Auditing the factory

The factory runs on its own (control plane tick every 15 minutes, agents in GitHub Actions, `apply-proposal.sh` mutating the target). This skill answers two questions with evidence: **what happened** in the window, and **did the plan in `HANDOFF.md` move**. It never changes anything.

## 1. Collect

Run the collector from the repo root and read the whole report:

```sh
.claude/skills/factory-audit/collect.sh --hours 24 > "$SCRATCH/audit.md"
```

- `--hours N` widens the window (72 after a weekend). `--no-artifacts` skips the per-phase `result.json` download (artifacts expire after one day anyway). `--no-logs` skips per-run log extraction, the slow part.
- It takes about three minutes for a normal day (one `gh run view --log` and one `gh run download` per agent run). Set `AUDIT_DIR` to keep the raw logs and artifacts somewhere you can grep afterwards.
- Needs `gh` logged in with read access to both repos, `jq`, `curl`. The control plane URL comes from the `FACTORY_URL` repository variable (or the env var).

The report has: control plane `/status`; factory commits; a run table with counts; per-run inputs, model switches, tool errors and the apply step's output; a `result.json` table per phase (stop reason, models, calls, tokens); target issues and PRs touched in the window with their timelines, reviews and checks; current backlog gates. Lines starting with `STALL:` are deterministic findings. They are leads, not verdicts: confirm each one in step 3.

## 2. Compare with the plan

Read `HANDOFF.md` `## Do next` and `**Open issues on fantasy-football**`. For every item the plan says should happen (a release, a run, a merge, a relabel, a close), find its evidence in the report: the run id, the label event, the PR, the comment. Classify each as done, in progress, stalled or not started. An item with no evidence is not started, whatever the prose says.

Also read the window's factory commits and `git status`: a fix landed yesterday should show up as a changed outcome today (a retry that succeeded, a skip reason that disappeared from the tick).

## 3. Verify, do not trust

For each run that succeeded, confirm its effect landed on GitHub: the apply step's last lines (`opened <PR url>`, `merge-policy: {...}`, `not mergeable: ...`, label edits) must match the issue timeline or PR section. A green run whose apply printed nothing, or whose labels did not change, is a silent failure.

For each run that did not succeed, read the lines at the `##[error]` markers (`harness failed for phase <p>` is followed by a `phase <p> result: stop_reason=... error=...` line with the models and switches, since 2026-11-12) and classify:

| Symptom | Meaning | Action |
|---|---|---|
| `HTTP 5xx`, `Gateway Timeout`, `invalid character 'S' looking for beginning of value` (a non-JSON 5xx body) | GitHub transient | check a later run retried and succeeded; `15c7e70` added retries to push, PR creation and label edits only |
| `startup_failure` with no jobs and no log | GitHub Actions did not start the job | expected noise if the next tick ran fine; the control plane marks it `lost` and alerts |
| `stop_reason` other than `end_turn` (`quota`, `latency`, `context`, `turns`, `output_guard`, `no_inputs`, `needs_human`) | a guard stopped the agent | compare with the `HANDOFF.md` run history for that issue; a repeat means the budget or prompt needs work, not a retry |
| `no successful phases to apply` | the harness never ran | look at the gather steps |

Known noise, do not report as a problem unless it changes:
- the reviewer runs hourly on the oldest open Dependabot PR and posts a `factory:review` comment without merging (`lit_dependencies` cover `wrangler` and `@cloudflare/*`);
- the incident responder's `honeycomb_query` and `inputs/honeycomb.json` errors (Honeycomb reads are disabled on the free plan);
- security-triage proposing nothing at 06:00 (all alerts already tracked);
- the Python `RuntimeWarning` about `output_guard` in the apply step.

Then check the gates for stalls the tick itself cannot see:
- a `lit` PR with a human `APPROVED` review, green checks and no open threads stays open until a push, a check suite or (Dependabot only) the hourly cron triggers the reviewer: a review event does not. While its issue is an open `bug` at `priority:1` or `priority:2` without `needs-human`, `bugs_first` also pauses the builder;
- `status:in-progress` with no run in the window means a run died without resetting the label;
- `status:in-review` with no open `feat/N-*` or `fix/N-*` PR means the PR merged or closed without the issue moving on;
- `in_flight` older than an hour in `/status` means a lost run;
- `today.model_calls` against the quota, and the per-run `total tokens` against the limits in `factory.config.json` for anything close to a cap.

## 4. Report

Write for the owner, who knows the system but did not watch it. Lead with the verdict in two or three sentences: healthy or stalled, what moved, what needs a human. Then:

1. **Runs**: a short table of counts by workflow and outcome, then each failure with its cause from the table above and whether it self-healed.
2. **Target movement**: issues and PRs that changed, with the run that changed them, in order.
3. **Stalls**: each confirmed stall, why the factory cannot clear it on its own, and the exact owner command that would (a `gh pr merge`, `gh issue edit`, or `gh workflow run`). Owner-only actions are never run by this skill: `gh workflow run`, label edits, merges, `wrangler deploy`, `/admin/pause`.
4. **Plan drift**: the `HANDOFF.md` items with their done / in progress / stalled / not started status and the evidence.
5. **Follow-ups for the factory itself**: anything the audit found that a code change should fix (a missing webhook event, a check that passed when it should not, a retry gap), as candidates for `## Known gaps` or `## Planned work`.

Cite run ids and issue or PR numbers so every claim can be opened. Numbers go in tables, not prose.

If the owner asks for the handoff to be updated, rewrite the `## Do next` block of `HANDOFF.md` with a fresh "checked <UTC timestamp>" and the state found here. Do not commit unless asked.
