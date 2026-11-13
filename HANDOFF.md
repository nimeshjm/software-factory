# Handoff: software factory

Written 2026-10-28, rewritten 2026-10-29 22:45 UTC, updated 2026-11-13 (revise loop, review-event and read-order fixes built after the 2026-11-13 audit found PR 129 stuck). Everything below is the state at that time.

## Do next (checked 2026-11-13 11:37 UTC; revise loop, review-event fix and read-order fix built 2026-11-13, uncommitted, not deployed)

**Revise loop, review event and inputs read-order (built 2026-11-13, uncommitted, not deployed).** Response to the 2026-11-13 audit: PR 129 (the 125 retry) sat as a draft with red CI after the reviewer asked for changes, because nothing revised a factory PR and the reviewer's `REQUEST_CHANGES` on the bot's own PR failed twice with HTTP 422 before landing as a plain comment. Contract: `CONTRACTS.md` `## Labels` → **Revise loop**, `## Post-step rules` → **Review event** and salvage step 5, `## inputs/*.json` (`factory_review`, inputs are inlined), `## Workflow wrappers` (`extra_phases`).

1. **Revise loop, one bounded attempt per PR.** Reviewer verdict `request_changes` on a factory PR (author `app/software-factory-agent-bot`, head `feat|fix/N-*`) → `apply_review` labels the PR `revise:requested` (or `needs-human` when `revise:attempted` is already there). The tick (`preflight.reviseCandidates`, before `candidateQueue` for bug-fixer and builder; ignores `backpressure` and `bugs_first`; subject `revise:N`, run id `<agent>-<stamp>-revise-N`) dispatches `{issue: N, pr, phase: "revise"}`. `agent-run.yml` checks out the PR head for that run (author guard: the App only), skips "Create work branch" and records the head in `inputs/branch.txt`. The new phase `revise` (`agents/<agent>/revise.md`, `extra_phases`, implement limits, `BuildProposal`) fixes only what the review and CI flagged; `apply_revise` pushes to the head, swaps the label to `revise:attempted`, runs `gh pr ready` when checks passed and comments `<!-- factory:revise -->`. CI's `check_suite completed` then re-dispatches the reviewer (a bot's `pull_request synchronize` is dropped). A revise that dies in the harness is salvaged by swapping the label and adding `needs-human` on the PR with the failure comment on the PR, no backup branch. Dashboard: revise items first in the builder and bug-fixer queue rows; a `revise:attempted` + `needs-human` PR reads `revise attempted; a human must take over`. Gates: a PR labelled `needs-human` or not authored by a bot login is never a revise candidate; the labels swap before the push and on every apply failure, so a revise never runs twice (only a cancelled or timed-out job leaves `revise:requested`, and the `revise:N` backoff spaces that retry). Reviewed with `/code-review medium` (ten findings; eight fixed, the full-budget reservation and the duplicated branch checks in `apply_revise` left as noted below).
2. **Review event.** On a factory-authored PR every review attempt uses `COMMENT` (no more 422s); the body's first line after the marker is the pipeline's `**Verdict:** <verdict>`, so the verdict survives.
3. **Inputs read-order.** Every prompt told the model to `read_file` `inputs/run.json`, which is outside the workspace and failed once per run (the incident-responder and verifier logs showed `read_file error - inputs/...`). The read-order line now says the inputs are already inlined in the task message. `pr.json` gains `factory_review` (the latest `<!-- factory:review -->` review body), inlined for `revise` and for the reviewer's next pass.
4. **Config.** `agents.<agent>.extra_phases: ["revise"]` and `phase_overrides.revise` (= implement) in `factory.config.json`; `check-config-sync.sh` sums budgets over `phases` only and checks an extra phase fits; labels `revise:requested`, `revise:attempted` (CI's `preflight-labels.sh` step creates them on the target at the next push).

*Verification (2026-11-13, after the build):*

| Check | Result |
|---|---|
| `uv run pytest -q` | 274 passed (250 before: revise apply and its failure paths, review event and the revise label rule, salvage revise, gather phases, config, prompts, fake-model revise run) |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 68 passed (61 before), clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean, `config sync OK` |
| prompt lint (one `{{target_profile}}`, mentions "proposal") over `agents/**/*.md` | clean |

*Owner actions:* review the diff, commit, push `main` (CI deploys the Worker and creates the two labels on the target). Then, to put PR 129 through the loop instead of fixing it by hand: `gh pr edit 129 -R nimeshjm/fantasy-football --add-label revise:requested` after the labels exist; the next tick dispatches `builder-<stamp>-revise-125` with the PR head checked out. Or fix the nested `it` blocks in `test/api.test.ts` yourself and `gh pr ready 129`.

*First live proof (2026-11-13 18:00 to 18:04 UTC): done.* The owner labelled PR 129 `revise:requested` at 17:50; the 18:00 tick dispatched `builder-202611131800-revise-125` (run 43478380232, subject `revise:125`, reserved 100). The run checked out the PR head, ran `revise` only, made one commit (`Fix test nesting in test/api.test.ts (#125)`), `run_checks` green, pushed, swapped the label to `revise:attempted`, readied the draft and posted the `<!-- factory:revise -->` comment. The check suite dispatched reviewer 43478758328 at 18:02:55: review body starts with `**Verdict:** approve`, agent checks all pass (110 lines, 3 files), no `review attempt rv1 failed` warning, `merge-policy: {"merge":false,"reasons":["autonomy:lit requires a human APPROVED review"]}`. PR 129 now waits for the owner's `APPROVED` review; the approval trigger merges it and closes 125. One question for the human in the review: whether `compareDreamTeam` belongs in `src/api/endpoints.ts`.

*Not done, deliberately:* no second attempt (one revise, then a human); the revise reserves the agent's full 100 calls (it uses at most 60); no per-phase timeout in the control plane; a backed-off revise does not appear in the tick's skip reasons; the dashboard's builder gate is computed from the issue queue only, so a row holding only revise items shows no gate.

**Gemini 4 (checked 2026-11-13 against aistudio.google.com/docs).** `gemini-4-argon` (GA 10-13) and `gemini-4-carbon` (GA 11-10) are paid only; `gemini-4-hydrogen` (GA 11-12, "recommended upgrade from Gemini 3.8 Flash") is free of charge on the free tier with rate limits shown only in AI Studio (https://aistudio.google.com/rate-limit, logged in), not in the docs. Added as the last fallback of `strong`, `spike_research` and `spike` (not `cheap`: Gemini 4 rejects `thinking_level: minimal`). To make it the spike research primary once its RPD is known, move it to `model_id` and keep 3.8-flash as a fallback; `max_output_tokens` then caps thinking plus output, so raise it to 32768 for that key. None of the current models has a shutdown date except `gemini-3.1-flash-lite` (2027-05-07). The 107 run of 14:30 UTC (43443387263) was the first live fallback chain on a spike: 3.8-flash and 3.6-flash unresponsive (server_error), research on 3-flash-preview, implement switched 3.5-flash-lite → 3.1-flash-lite on a throttle, findings posted, 107 closed.

**Audit 2026-11-13 11:37 UTC (`factory-audit`, 24 h window).** Healthy but idle. The salvage build proved itself on the 125 retry (run 43264835535, 17:00 to 17:11 UTC): `shell_in_workspace blocked - npm run typecheck && npm test` once, `budget_guard` `soft_nudge` at 802,543 of 765,000 then `finalize`, implement ended at 890,554 of 900,000 tokens over 37 calls, PR 129 opened. First live model fallback: `gemini-3.5-flash-lite unresponsive (server_error); switching to gemini-3.1-flash-lite`, twice in that run and once in reviewer 43266897263 (so on flash-lite, not 3.8-flash). PR 129 is a draft (the agent's own checks failed), build-and-test fails with 18 errors (new tests nested inside an existing `it`), reviewer verdict `request_changes` posted as a COMMENT after two 422s; issue 125 is `status:in-review`. Since 17:16 UTC the tick has had no eligible work (107 `failure-cap:2026-11-13T13:15Z`, so the 13:30 tick retries it; 116 waits for `plan:approved`; epics 35, 106, 115 in `plan-review needs-human`; 100 to 103 and 108 to 110 `author-not-trusted`; 117 and 119 to 121 `needs-human`). Model calls at 11:30 UTC: 5 of 400. Security triage at 06:00 proposed `resolved` for 119 to 121 and the apply skipped all three (alerts still open); the incident responder's `read_file error - inputs/run.json` is the read-order noise fixed above.


**Failed implement runs: salvage, budget wrap-up, cheaper test output (built 2026-11-12, uncommitted, not deployed).** Prompted by the 125 run (below). Contract in `CONTRACTS.md`: the salvage bullet under Post-step rules, `backup/<N>-attempt<k>-<stop_reason>` under Branch conventions, and "Budget wrap-up" plus "Shell tool and context size" under Harness CLI.

1. **Salvage post-step** (`scripts/salvage-run.sh`, step "Salvage failed run" in `agent-run.yml`, after "Apply proposal"): for a failed builder or bug-fixer run with an issue, deterministic and model-free: when the failed phase is `implement`, WIP-commits `work/` and pushes it to `backup/<N>-attempt<k>-<stop_reason>` (`out/salvage.txt` holds the name); moves `status:in-progress` back to `status:backlog` (other status labels are left alone, so a plan hold stays `plan-review`); posts one `<!-- factory:failure -->` comment with phase, stop reason, error, backup branch, label action and run link. Never fails the job. Tested against a real bare repo and a stub `gh` (`tests/test_salvage.py`). The control plane's failure backoff spaces the retries (60 min, 180 min, 24 h cap), so the reset cannot recreate the 107 loop.
2. **Budget wrap-up** (`factory/guards/budget_guard.py`, `factory/run.py`): at 85% of `total_tokens`, `turns` or model calls (calls also leave a reserve of 3) the guard ends the turn with `[budget_guard] budget nearly spent, finish now with the proposal`; `factory.run` then gives one `FINALIZE_PROMPT` invocation (3 turns: commit if needed, emit the proposal) after a budget or latency nudge, and also after a bare `limit_turns` / `limit_total_tokens` stop. Guard events `budget_guard/soft_nudge` (`tokens=<used>/<cap>`) and `budget_guard/finalize` (`<first stop_reason>`) in `result.json`. `context.budget_soft_fraction` in `factory.config.json` overrides the fraction (not set; default in code).
3. **Cheaper test output**: `ToolGuard` blocks any `shell_in_workspace` command equal to (or starting with) one of the profile's `toolchain.checks[].run` as `use_run_checks`, so the full suite, lint and typecheck only reach the model as `run_checks`' summary; `context.tool_result_max_chars` 20000 → 8000 (`fetch_url` is exempt: it clips itself to 20,000 and research needs its key tree whole); both implement prompts say so, name both nudges, and tell the model not to re-read files.

*Verification (2026-11-12, after the build):*

| Check | Result |
|---|---|
| `uv run pytest -q` | 250 passed (226 before: 4 budget guard, 2 wrap-up incl. a `--fake-model` run that hits `limit_turns` and finishes via the finalize, 5 salvage, 3 context guard, 10 tool guard) |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 61 passed, clean, succeeds (no control plane change) |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |

*Owner actions:* review the diff, commit, push `main` (CI deploys nothing new here: the harness, scripts and workflow are read from `main` at the next run; the Worker is unchanged). Then release 125: `gh issue edit 125 -R nimeshjm/fantasy-football --remove-label status:in-progress --add-label status:backlog` (it carries one failure, so the tick waits 60 minutes from 16:11 UTC before retrying; or dispatch by hand with `gh workflow run builder.yml -R nimeshjm/software-factory -f issue=125`). The 125 work from run 43253957669 is gone (the salvage step did not exist yet).

*First live proof:* on the 125 retry, `[tool] shell_in_workspace blocked` lines for any `npm test` in the implement step log, a `budget_guard` `soft_nudge` event only if it again nears 900k, and either a PR or, on failure, a `<!-- factory:failure -->` comment on 125, a `backup/125-attempt1-<reason>` branch and `status:backlog` restored without a human. The dashboard should then never show `stuck-in-progress` for a run that died after apply; if it does, the salvage step's log says why.

*Not done, deliberately:* no rtk (the harness has no command-rewrite hook, and `run_checks` already summarises); no resume of a backup branch on retry (the retry re-runs research and plan from `status:backlog`; cheap on flash-lite); backup branches are not cleaned up (delete `backup/*` by hand when an issue closes, or add a cron later).

**The 125 run (43253957669, 2026-11-12 16:00 to 16:11 UTC).** First run on 125. Research and plan succeeded; apply posted the plan and set `status:in-progress` at 16:10:53. Implement died with `stop_reason=limit_total_tokens`: 42 calls, 30 cycles, 921,787 tokens (919,399 input) against the 900,000 cap, no proposal, nothing pushed, so the edits (endpoint, test, spec; `run_checks` had just returned ok twice) were lost. Two unresponsive-model switches to `gemini-3.1-flash-lite` (throttled 16:02, server_error 16:08) worked as designed and were not the cause. The budget went on context growth: three bare `npm test` runs through `shell_in_workspace` (two results truncated at 20,000 chars) and repeated re-reads of `src/api/endpoints.ts` and `test/api.test.ts`, about 22k input tokens per call. 125 is still `status:in-progress` with no run (the 16:15 tick's skip list does not mention it): the dashboard shows `stuck-in-progress`. The three fixes above are the response.

**Plan C: agents run health board in Honeycomb (planned 2026-11-12 17:10 UTC, nothing built).** Scope decided by the owner: a board that answers "are the agents' runs healthy?", not the factory-operations board that software-factory issue #1 describes. Issue #1's tick-decision, PR-opened/merged and daily-quota panels are deferred; the owner should retitle or split #1 (the run panels, the `workflow_run_url` field, the `docs/honeycomb/` and CONTRACTS criteria carry over). Researched 2026-11-12 against the Honeycomb docs: the free plan allows the Boards, Queries, Query Annotations and Calculated Fields (derived columns) APIs with a configuration key; only the Query Data API is Enterprise. Boards are `flexible` only since 2025-08-15, a query panel needs a `query_id` and a `query_annotation_id`, `position` is `x,y,w,h` on a 12-column grid. Retention is 60 days from ingest, so the 11-11 and 11-12 runs stay queryable into January. Span events carry the OTLP resource attributes (`factory.run_id`, `factory.agent`, `factory.phase`), so guard and model events are queryable by agent without changes.

*What the board shows (dataset `factory`, EU, every panel last 7 days by default; one saved query, annotation and panel each):*

| # | Panel | Reads | Needs |
|---|---|---|---|
| 1 | Run outcomes per day: COUNT `name=run.finished` by `agent_d`, stacked by `status`, 1-day granularity | `run.finished` | `agent` on the event; `agent_d` derived column backfills old events from `run_id` |
| 2 | Where runs die: table of `run.finished` with `status in (failed, timeout, lost, denied)`: `run_id`, `agent_d`, `subject`, `phase`, `stop_reason`, `error`, `workflow_run_url` | `run.finished` | `subject`, `phase`, `error`, `workflow_run_url` |
| 3 | Stop reasons by agent: COUNT by `agent_d`, `stop_reason` | `run.finished` | nothing new |
| 4 | Run time: P50/P95 `duration_s` (started to finished) and P95 `queue_s` (dispatched to started) by `agent_d` | `run.finished` | `duration_s`, `queue_s` |
| 5 | Model calls vs cap: HEATMAP `model_calls` by `agent_d`; MAX `calls_ratio` by `agent_d` per day (1.0 = hit the cap) | `run.finished` | `reserved_calls`; `calls_ratio` derived column |
| 6 | Tokens per phase and model: SUM `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` by `factory.agent`, `factory.phase`, `factory.model_id` | harness model spans | nothing new (the 3x gap against `result.json`, HANDOFF 2026-10-30, stays unexplained) |
| 7 | Model health: COUNT `name=factory.model_switch` by `factory.model.from`, `.to`, `.reason`; COUNT `name=factory.model_retry` by `factory.agent`, `factory.retry.reason`, `factory.model_id` per day; SUM `throttles` by `agent_d` | harness span events, `run.finished` | new `factory.model_retry` span event; `throttles` on `run.finished` |
| 8 | Guard denials: COUNT `name=tool_guard.blocked` by `factory.agent`, `rule`; COUNT `name ends-with .event` by `guard`, `rule` | harness span events | nothing new |
| 9 | Lost and dry runs: COUNT `run.finished` `status=lost` by `agent_d`; everything above filterable on `dry_run` | `run.finished` | `mark_lost` must emit `run.finished`; `dry_run` on the event |

*Emitter gaps found 2026-11-12:* `run.finished` (`control-plane/src/ledger.ts:62`) carries only `run_id`, `status`, `stop_reason`, `model_calls` and `usage` as a JSON string, so nothing on it groups by agent or links to the run; `mark_lost` (`scheduler.ts`) updates the ledger and alerts but sends no `run.finished`; `ledger-post.sh finished` does not say which phase failed or why; the harness has `factory.model_switch` and `tool_guard.blocked` span events but no event per throttle, stall or 503 retry (`result.json` has the `throttles` count only); the tick's `skip` reason strings embed per-issue detail (`no_eligible_feature (#107:failure-cap:...)`), which only matters for the deferred ops board.

*Work packages (contract first, then 2 to 5 in parallel, disjoint files):*

1. **Contract** (main session): new `CONTRACTS.md` section `## Honeycomb events` listing every event name and field: `run.started {run_id, agent, subject, phase, trigger, workflow_run_url, queue_s}`; `run.finished {run_id, agent, subject, trigger, status, stop_reason, phase, error, model_calls, reserved_calls, throttles, usage.input_tokens, usage.output_tokens, usage.total_tokens, duration_s, queue_s, issue_number, pr_number, workflow_run_url, dry_run}` (also for `lost`, from the tick); `tick.decision`, `tick.error`, `webhook.decision` as today; harness spans and span events (`factory.tool_call`, `tool_guard.blocked`, `<guard>.event`, `factory.model_switch`, new `factory.model_retry {factory.model_id, factory.retry.reason: throttled|stall|server_error|daily_quota, factory.retry.attempt}`); the two derived columns; and the `docs/honeycomb/` file shapes. Extend `POST /runs/:id/finished` with optional `phase`, `error` (300 chars), `throttles`, `dry_run`.
2. **Control plane** (`ledger.ts`, `scheduler.ts`, `honeycomb.ts`, tests): a pure `finishedEvent(row, body, now)` builder used by the handler (always reads the run row; `duration_s` and `queue_s` from the row's timestamps, null when never started) and by `mark_lost` (`status: lost`, `stop_reason: timeout_no_report`, row from `state.inFlight`); flatten `usage`; `run.started` gains `subject`, `trigger`, `workflow_run_url`, `queue_s`. Tests on the builder in `ledger.test.ts`, plus one `sendEvent` test with a stubbed `fetch` and `HONEYCOMB_API_KEY` set, asserting the posted body. `control-plane/README.md` subrequest note: one extra D1 read per finish, one extra event per lost run.
3. **Scripts and workflow** (`scripts/ledger-post.sh`, `.github/workflows/agent-run.yml`, `scripts/README.md`): the finished payload adds `phase` (first phase whose `result.json` has `success: false`, else the last phase run), `error` (that result's `error`, clipped), `throttles` (sum), `dry_run` (from the workflow input, passed as `DRY_RUN`). Tested like `run-phases.sh`: stub `curl` capturing the body, fixture `out/phases/*/result.json`.
4. **Harness** (`factory/gemini.py`, `tests/test_gemini_retry.py`): `span.add_event("factory.model_retry", ...)` at the three retry points (throttle, stall, retryable 503 and transport error) and on the daily-quota switch path, next to the existing stderr lines; test asserts the events with the fake tracer the `model_switch` tests use.
5. **Board as code** (`docs/honeycomb/`, `scripts/honeycomb-board.sh`, `README.md`, `scripts/README.md`): `docs/honeycomb/derived_columns.json` (`agent_d` = `COALESCE($agent, $factory.agent, REG_VALUE($run_id, `^([a-z]+(-[a-z]+)*)-[0-9]{12}`))`, `calls_ratio` = `DIV($model_calls, $reserved_calls)`), `docs/honeycomb/panels/<nn>-<name>.json` (annotation name and description, query spec, `query_style`, `position`) and `docs/honeycomb/board.json` (name `factory`, description, `layout_generation: manual`, board id once created). The script (owner-run, `HONEYCOMB_CONFIG_KEY` env, `api_base` from `factory.config.json`, `--dry-run` prints the requests) upserts the derived columns by alias, creates each query and annotation (queries are immutable, so every run makes fresh ones), then creates the board or `PUT`s its panels, and prints the board URL. The README `## Honeycomb board` section becomes the link plus the panel table above with the fields each reads.

*Verification, in order:* `uv run pytest -q`, `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run`, `actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh`; `honeycomb-board.sh --dry-run` against the checked-in JSON. Then owner actions: push `main` (CI deploys the Worker); create a Honeycomb configuration key for the `factory` environment with "Manage Queries and Columns" and "Manage Public Boards" and run `HONEYCOMB_CONFIG_KEY=... scripts/honeycomb-board.sh`; first settle the two unverified points with one query each in the UI: `REG_VALUE` returns the first submatch (else `agent_d` shows `builder-202611121315`) and whether `POST /1/queries/__all__` works on the free plan (not needed: the board uses the `factory` dataset). Proof against real data: panel 1 and 2 show the 2026-11-11 bug-fixer runs on fantasy-football 118 (43044016537 failed at apply with the 504, 43051531921 success) and the 2026-11-12 107 loop (43225593828, 43227971800, 43237920290, 43243138668, 43245862580: `timeout` and `exception`, 1 to 6 calls, 0 tokens), via `agent_d` since those events predate the new fields; panel 7 shows its first `factory.model_switch`/`factory.model_retry` after the next 3.8-flash throttle; panel 6 already has data (dry run 41053297498). The first run finished after the deploy is the first event with `phase`, `error` and `workflow_run_url`.

*Cost and risk:* low. Additive event fields and one new span event; no dispatch, merge or write path changes. Board, queries and derived columns are free-plan features; the configuration key is used only from the owner's machine and never stored in the repo or Actions. If `REG_VALUE` gives the whole match, the fallback is `agent_d` from `run.started` events only for the backfill window, and the panel reads agent-tagged events from the deploy onward.

**Unresponsive-model fallback and per-issue failure backoff (built 2026-11-12 15:30 UTC; commit `f13f122`, deployed by CI run 43251252769 at 15:46 UTC).** Three fixes for the 107 retry loop below; contract in `CONTRACTS.md` (`**Unresponsive model fallback:**` under Web research and spikes; "Failure backoff" under Control plane HTTP).

1. **Harness** (`factory/gemini.py`): a request that yields no content and ends in a non-daily-quota throttle, a 503, an `httpx` transport error, or no first event within `first_event_timeout_s` (150 s) counts as *unresponsive*; two in a row on the same model (`unresponsive_switch_after`) switch to the next `fallback_model_ids` entry, the same way a daily-quota 429 already did (stderr `[model] <old> unresponsive (<detail>); switching to <new>`). Below the threshold strands backs off and retries as before. `result.json` gains `model_switches[{from, to, reason}]`. Both knobs are optional per `models.<key>` in `factory.config.json`; defaults are in code.
2. **Control plane** (`control-plane/src/preflight.ts` `failureBackoff`, used by `candidateQueue` so the tick and the dashboard agree): an issue whose last run ended `failed`, `timeout` or `lost` is skipped for 60 minutes after one failure, 180 after two, and for 24 hours after three (`retry-after:<time>` / `failure-cap:<time>` as the skip reason), counted from the ledger's last 24 hours and reset by a `success`. The tick walks on to the next candidate instead of stalling. The dashboard shows `backoff` (waits) and `failure-cap` (you, with the manual `gh workflow run` to retry now). `loadTickState` now reads `status` with the recent runs.
3. **Step log** (`scripts/run-phases.sh`): a failed phase now prints a second `::error::` line with `stop_reason`, `model_calls`, `total_tokens`, the models used, the switches and `error` from its `result.json`, so a dead run is readable without downloading the artifact (`CONTRACTS.md` Post-step rules; tested with a stub harness).

**Live since 15:46 UTC.** The owner had already parked 107 by hand at 15:23 (`status:in-progress`, so the dashboard shows it as `stuck-in-progress`; that is the parking, not a dead run). The 15:30 tick planned epic 115 (run 43248557675, flash-lite, 13 calls; now `status:plan-review needs-human`) and the 15:45 tick dispatched 116 for its plan (run 43251271748). When 107 goes back to `status:backlog` it carries six failures from today, so the tick keeps it `failure-cap` until about 15:15 UTC on 2026-11-13; to run it sooner, dispatch by hand once 3.8-flash answers again: `gh workflow run builder.yml -R nimeshjm/software-factory -f issue=107`. The harness fallback applies to every run started after 15:46; its first live proof is a `[model] gemini-3.8-flash unresponsive (...)` line in a research step log.

**Verification (2026-11-12, after the build):**

| Check | Result |
|---|---|
| `uv run pytest -q` | 226 passed (216 before) |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 61 passed (48 before), clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| `run-phases.sh` with a stub `uv` that writes a `result.json` and exits 1, and one that exits 2 | prints `phase research result: stop_reason=timeout ... switches=gemini-3.8-flash>gemini-3.6-flash (unresponsive:throttled) error=...` and `phase research wrote no result.json` |

Deployed by CI; no run has exercised the fallback yet. The backoff is live in the tick (no candidate currently has a recent failure, since 107 is parked). The first live proof is the next 3.8-flash research run that hits a throttle: look for `[model] gemini-3.8-flash unresponsive (...)` in the step log and `model_switches` in `phases/research/result.json`; and the next tick after the push, whose `last_tick.decisions` should list `#107:failure-cap:<time>` and dispatch the next candidate.

**The 107 retry loop (found 2026-11-12 15:20 UTC).** `gemini-3.8-flash` stopped answering at about 13:15 UTC and the builder retried 107 on every free tick. The label only moves in the apply step (`scripts/apply-proposal.sh`), so a harness failure left 107 in `status:backlog` and the tick re-picked it; the fallback in `factory/gemini.py` only switched on a daily-quota 429, so a throttled model spent each phase's whole budget; strands retried each throttle six times with backoff, hence six calls and zero tokens per run. (The earlier note that a failed run leaves the issue `status:in-progress` was wrong: that only happens once apply has started.)

| Run (UTC) | Research outcome |
|---|---|
| 43225593828 (13:15) | `timeout`, `latency_guard hard timeout`, 3 calls, 0 tokens |
| 43227971800 (13:30) | `exception`, `ReadError:`, 1 call |
| 43237920290 (14:30) | `timeout`, `ModelThrottledException: Deadline expired before operation could complete.`, 6 calls, 0 tokens |
| 43243138668 (15:00) | `exception`, `ModelThrottledException: This model is currently experiencing high demand...`, 6 calls |
| 43245862580 (15:15) | `timeout`, `ModelThrottledException: ... high demand ...`, 6 calls, 0 tokens |

Each attempt costs about six model calls and twenty minutes of runner time; model calls stood at 110 of 400 at 15:30 UTC, and the 15:30 tick will dispatch 107 again until this is pushed (or 107 is parked: `gh issue edit 107 -R nimeshjm/fantasy-football --remove-label status:backlog --add-label status:in-progress`). Between the failures the 14:00 and 14:15 ticks planned epics 35 and 106 on flash-lite without trouble, so only 3.8-flash was affected.

**Everything else on 2026-11-12 (checked 15:20 UTC):**
- **Epics 35 and 106 were planned** (runs 43232794169 and 43235336033) and sit in `status:plan-review needs-human` with their plan comments. **Epic 115** is still `status:backlog risk:high` and is the next builder candidate once 107 is capped; `candidateQueue` still has no `epic` exclusion, so either add one or accept the plan run.
- **119, 120, 121** (security bugs) are `needs-human`, unchanged; the bug-fixer skips them every tick. PR 88, the `vitest` bump that would close 121, still fails `toolchain.setup`, so the reviewer posts "could not set up this branch" and leaves it `needs-human`.
- **100 to 103 and 108 to 110** are `author-not-trusted` (bot-filed, no `factory:accepted`); the dashboard lists the label command for each.
- **116, 117** are backlog and healthy. 116 is `risk:high` and gets a plan run when its turn comes; 117 is `needs-human`. **125** ran at 16:00 UTC and died in implement on the token cap (see "The 125 run" above); it is parked `status:in-progress` until the owner releases it.
- **118 closed** `status:done` via PR 126 (merged 13:12 UTC).
- **Yesterday's two failed runs self-healed:** bug-fixer 43044016537 (15:00, apply 504) was followed by the good 15:45 rerun; reviewer 43059024527 (16:30, `gather-inputs.sh json` exit 1, transient) was followed by a clean 17:30 run. Reviewer 43096480238 (20:30) was a `startup_failure` with no jobs, known noise.
- **Not yet adopted on the target:** `nimeshjm/fantasy-football` `factory.yml` has no `amigos`, `plan_review` or `limits.max_context_chars` blocks, so plan comments carry no amigos row and any human approval merges. Copy them from `examples/fantasy-football/factory.yml` when wanted.
- **Software-factory repo** has one open issue, #1 (Honeycomb board for factory operations); nothing acts on it. Plan C above covers its run-health subset; the rest is deferred.

**Earlier on 2026-11-12 (checked 14:10 UTC).**

**Dashboard (built 2026-11-12 14:10 UTC, uncommitted in the working tree).** `GET /dashboard` on the control plane: what happened, what is blocked (with the command that clears each item), what the next tick will do. Contract in `CONTRACTS.md` `## Control plane HTTP`, runbook in `control-plane/README.md` `## Dashboard`, code in `control-plane/src/dashboard.ts` (pure build and render, tested) with the route in `src/index.ts`. `preflight.ts` gained `candidateQueue`, `urgentBugs` and `countFactoryPRs`; `planTick` now walks `candidateQueue`, so the forecast and the tick cannot disagree (behaviour unchanged, 37 existing tests pass as before). `github.ts` issue and PR rows carry titles, authors, timestamps, `html_url`, and `listIssues` takes `since` and `includePRs`. Nothing else changed. Push `main` and CI deploys it; then open `https://factory-control-plane.nimeshjm.workers.dev/dashboard`.

| Check | Result |
|---|---|
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 48 passed (37 before), clean, succeeds |
| Preview rendered from live target data and `/status` (Actions runs standing in for the ledger) | desktop and phone screenshots read; blockers grouped by kind |

**Two things the dashboard preview showed on 2026-11-12 (verified against `/status` at 14:07 UTC):**

1. **The builder is planning epics.** Since the three amigos change dispatches `risk:high` features for a plan, the backlog epics 35 ("add a second LLM provider"), 106 and 115 (`risk:high status:backlog priority:2`, labelled `epic`) are builder candidates, and 35 sorts first. The 14:00 tick dispatched `builder-202611121400-issue-35` (run 43232794169) on the epic itself, and 106 and 115 follow. `candidateQueue` has no `epic` exclusion; add one (and a test) if epics should never be built or planned directly, or relabel the epics out of `status:backlog`. Each plan costs a research plus plan run.
2. **107 failed twice today, both in research before any tool call, on `gemini-3.8-flash`** (read 14:15 UTC). Run 43225593828 (13:15): 3 model calls, 0 tokens, `stop_reason: timeout`, `error: latency_guard hard timeout` (the research phase's 10-minute budget ran out with no response). Run 43227971800 (13:30): 1 call, `error: ReadError:` (the stream dropped), same stop reason. The step log shows only the `##[error]harness failed for phase research` line; the detail is in the artifact's `phases/research/result.json`. Nothing in the harness or the issue changed between the 10-30 dry run that passed and these runs, so this reads as 3.8-flash not answering between 13:15 and 13:46 UTC. The issue never left `status:backlog` (no label event today), so the tick re-picks it in order. Two gaps for the factory: `factory/gemini.py` falls back to the next model only on a daily-quota 429, so an unresponsive model spends the whole phase budget (a hard timeout or `ReadError` on a call that produced no tokens could switch models the same way); and the apply step's log should print `stop_reason` and `error` from `result.json` on failure, so a failed run can be read without downloading the artifact. The 13:17 reviewer run 43225912189 on PR 88 also failed (PR 88 is `needs-human`).
3. **Epic 35 was planned** by run 43232794169 (14:00 to 14:11, research and plan on flash-lite, 20 calls, 112k tokens): plan comment posted with a seven-file plan, labels now `status:plan-review needs-human`. Point 1 above stands: 106 and 115 are next unless the epics leave `status:backlog` or `candidateQueue` excludes `epic`.

The dashboard was deployed by CI run 43234781257 (commit `439dc38`) and is live at `https://factory-control-plane.nimeshjm.workers.dev/dashboard`.

The seven child issues 100 to 103 and 108 to 110 are `author-not-trusted` (filed by the bot, no `factory:accepted`); the dashboard lists the `gh issue edit ... --add-label factory:accepted` for each.

**Three amigos change (built 2026-11-12 13:40 UTC, uncommitted in the working tree).** Implements the four "Monday morning" items of https://nimeshjm.com/blog/ai-new-3-amigos-agent-orchestration/ on top of the agent trio the factory already had; the written boundary is `docs/three-amigos.md`, the shapes are in `CONTRACTS.md` (`## Three amigos` and the sections it points at).

1. **Humans named per target.** `factory.yml` `amigos: {product, development, testing}` (GitHub logins; a login in two lists is recorded as two hats). The pipeline mentions them in an `Amigos` row of every plan comment and a footer on every review; prompts never see them. `merge_policy` accepts a lit PR's `APPROVED` review only from a named amigo once any are named (`factory/policy/merge.py`). The feature issue form gained an optional "Three amigos for this work" field (not parsed).
2. **The plan is read before code, for every held issue.** The control plane now dispatches `risk:high` features (before, they were skipped until `plan:approved` existed, so the human approved a plan nobody had written) and resumes `status:plan-review` + `plan:approved` issues for `implement` (`control-plane/src/preflight.ts`, skip reason `awaiting-risk` replaces `awaiting-risk-or-plan-approval`). The hold is one pure function, `factory.policy.plan_gate`, driven by `plan_review.hold_on` (default `["risk:high"]`; add `size:L` to review more plans) and `needs_human`, called by both `run-phases.sh` and `apply_plan`. `apply_plan` prepends a `### Plan at a glance` table (steps, files, needs a human, gate, amigos) rendered from the proposal's structured fields.
3. **Review split sharpened.** Each question for the human is prefixed with the role it is for: `(product)`, `(development)`, `(testing)` (reviewer prompt step 4, `review-template.md`).
4. **Standards stay small.** `limits.max_context_chars` (60,000) caps the context files inlined into every prompt; the harness stops before any model call with `stop_reason: context_oversize`, and `python -m factory.schemas check-context` runs the same check in CI (`profiles` job) and can run in the target's CI (`templates/target/ONBOARDING.md`).

**What changes live when this is pushed:** CI deploys the Worker (control-plane changed). On the next ticks the builder will plan, one issue at a time, every `risk:high` feature that is `status:backlog` and passes the author gate (116 "Remove API-Football" first; some of 100 to 103 and 108 to 110 if they are `risk:high` rather than author-gated), parking each in `status:plan-review` with its plan comment. Each plan costs a research plus plan run on flash-lite (about 40 of the 400 daily calls). Pause the control plane first if that is not wanted today. The live target profile in `nimeshjm/fantasy-football` does not need to change (every new section has a default: no amigos means no footer and any human approval still merges), but to get the footers and the amigo-only approval rule copy the `limits.max_context_chars`, `amigos` and `plan_review` blocks from `examples/fantasy-football/factory.yml` into the target's `factory.yml` on `main`.

**Verification (2026-11-12 13:40 UTC):**

| Check | Result |
|---|---|
| `uv run pytest -q` | 216 passed (196 before) |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 37 passed, clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| `validate-profile` and `check-context` on `templates/target` and `examples/fantasy-football` | pass; example context files total 20,495 chars |
| `apply-proposal.sh builder plan` against a stub `gh`, real `plan-gate` and `amigos` CLIs, issue labelled `risk:high` | comment rendered with the at-a-glance table, gate `held: label:risk:high; add plan:approved to continue`, amigos row with `two hats: @nimeshjm (product, development, testing)`; labels `status:plan-review` |

Not exercised live: no workflow dispatched, nothing deployed, nothing committed.


**Approval trigger: live and proven (2026-11-12).** The control plane dispatches the reviewer when a human submits an `APPROVED` review (`control-plane/src/webhooks.ts`, `mapEvent` and `debounceWindowMs`; commit `89f2676`, deployed by CI run 43223444143; hook 704460903 subscribed to `pull_request_review` at 13:11 UTC). First live use: the owner re-approved PR 126 at 13:11, reviewer run 43224911506 was dispatched within seconds, merge-policy answered `{"merge":true,"reasons":["human approved"]}` and squash-merged at 13:12; issue 118 closed as `status:done`.

**Then, within five minutes (13:13 to 13:17 UTC):** the owner approved Dependabot PRs 128 (`wrangler` 4.158.0) and 127 (`@cloudflare/workers-types`); the trigger dispatched reviewer runs 43225315417 and 43225329072, both answered `human approved` and merged. The Deploy of the 126 merge (run 43225059867, sha `76264a9`) had failed with Cloudflare API error 10013 on the secrets endpoint, but the two merges that followed deployed cleanly (`76339eb`, `fafacb8`), so the live site carries all three changes and the 10013 was a transient. Verifier runs 43225622953 (passed) and 43225740194 followed the deploys. With 118 closed, the 13:15 tick dispatched the builder on 107 (run 43225593828, `builder-202611121315-issue-107`): the first spike apply against GitHub, live. Reviewer run 43225912189 then picked up PR 88 (the lit `vitest` group bump, `needs-human`) from a check-suite event.

**Check next:** the 107 run. Success is a `<!-- factory:findings -->` comment on 107 ending in `## Evidence`, `status:done`, issue closed. If it fails in the harness, the issue stays `status:backlog` and the tick retries it (subject to the failure backoff above); read `stop_reason` and compare with the 107 history below. 125 (`risk:low size:S`) already ran and failed on 2026-11-12 16:00; release it as described at the top of Do next. `/status` `last_tick.decisions` and the `factory-audit` skill (`.claude/skills/factory-audit/`) show both.

**Earlier state (checked 2026-11-02 15:50 UTC), kept for the history of 107 and 118.**

**State on Monday afternoon.** Security-triage is live and ran on its own at 06:00 UTC on 2026-10-31, 11-01 and 11-02 (runs 41174823542, 41279077141, 41397294396), one flash-lite call each. The first run created issues 118 to 121 on fantasy-football; the next two recognised all four alerts as tracked and proposed nothing, so dedup works. Issue 107 is still `status:in-progress`, untouched; flash quota is fresh (no builder run since Friday's dry run). Two problems block the plan below, so fix them first.

**Problem 1 (fixed 2026-11-11, `urgentBug` now ignores `needs-human`; 118 relabelled `risk:low` the same day and released to the bug-fixer): the security bugs pause the builder.** The control plane's tick reports `skip builder bugs_first`: `preflight.ts` (around line 218, `urgentBug`) pauses features whenever any open `bug` carries `priority:1` or `priority:2`, and it does not exclude `needs-human`. All four security issues are `needs-human`, and the three transitive-dependency ones (119 `sharp`, 120 `undici`, 121 `@vitest/mocker`) stay open until a human acts, so the builder is blocked indefinitely. Releasing 107 does nothing until this is fixed.
- Fix: `urgentBug` ignores issues labelled `needs-human` (a bug waiting on a human should not block agent work). Add a test in `control-plane/test/preflight.test.ts` next to the existing `bugs_first` case (line ~54), update the `bugs_first` sentence in `control-plane/README.md` (line ~102) and the `**Pickup**` line of `CONTRACTS.md` `## Security triage`. Push `main`: CI deploys the Worker.

**Problem 2: issue 118 has the wrong labels.** The code-scanning fix (`js/stack-trace-exposure`, `src/loginProbe.ts:73`) should be `risk:low size:S`, no `needs-human`, as the dry run produced. The live run gave it `risk:high size:S needs-human`. The apply script adds `needs-human` only for secret scanning and non-direct dependencies, so flash-lite put it there itself; the artifact (1-day retention) is gone and the apply step's stderr did not reach the Actions log, so the live proposal cannot be shown.
- Fix in code: `apply_security` strips `needs-human` from `code_scanning` findings (the rules never allow it there); risk stays the agent's call. Record it in `CONTRACTS.md` `## Security triage`, Apply step 2.
- Fix on the issue: `gh issue edit 118 -R nimeshjm/fantasy-football --remove-label needs-human,risk:high --add-label risk:low`. Once 118 is `risk:low` without `needs-human`, the bug-fixer takes it within 15 minutes: its first ever run, live. To see it first: `gh workflow run bug-fixer.yml -R nimeshjm/software-factory -f issue=118 -f dry_run=true` before relabelling, and check its proposal (a `fix/118-...` branch touching only `src/loginProbe.ts`). While 118 is open and `priority:2`, `bugs_first` (even after the fix) pauses the builder, so order matters: either release 107 before relabelling 118, or accept that 107 waits for the bug-fixer.
- Also worth fixing while there: `log` lines from `apply-proposal.sh` go to stderr but did not show in the run log, so a live apply leaves no trace of its decisions except the created issues. Check how the apply step captures stderr in `agent-run.yml`.

**Then the plan as before:**
1. **Release 107:** `gh issue edit 107 -R nimeshjm/fantasy-football --remove-label status:in-progress --add-label status:backlog`. The cron runs it live within 15 minutes (only once `bugs_first` no longer skips the builder; check `/status` `last_tick.decisions`). First live use of the spike apply branch against GitHub.
2. **Check the live run** the same way as the dry run: `[tool]` and `[model]` lines in the step log; `phases/<phase>/result.json` for `model_ids_used`, `model_calls`, `usage.total_tokens`; `check-findings` on the artifact; Honeycomb filter `factory.run_id = gh-<run id>-1`. Success: a `<!-- factory:findings -->` comment on 107 ending in `## Evidence`, `status:done`, issue closed. The 107 dry run (41053297498, 2026-10-30) passed: research on `gemini-3.8-flash`, 22 calls, 366k tokens; implement on `gemini-3.5-flash-lite`, 9 calls, 185k tokens; findings verified by hand.
3. **If it fails:** the issue stays `status:in-progress`. Read `stop_reason` and compare with the 107 history below. Only two spikes' worth of flash exists per day, so a retry means the next day.
4. **Then release 117** (`gh issue edit 117 -R nimeshjm/fantasy-football --remove-label needs-human`), the next day.
5. **Issue 116 (remove API-Football)** is `risk:high`: it waits for `plan:approved`, its PR needs your review, and you must remove the `API_FOOTBALL_KEY` line from fantasy-football's `factory.yml` yourself.
6. **The three `needs-human` security issues** (119, 120, 121) are yours: `undici` and `@vitest/mocker` are transitive via `wrangler` and `vitest`, so the fix is the Dependabot bumps (PRs 112 and 88, both lit, waiting for your `APPROVED` review); `sharp` has no Dependabot PR. When an alert closes on `main`, the next 06:00 run comments on its issue, sets `status:done` and closes it.

**Plan B roll-out record** (all done 2026-10-30): App permissions added and accepted; pushed as `bbcc1d4`, Worker deployed, `security` label synced; target hook 704460903 subscribed to `code_scanning_alert` and `dependabot_alert`; dry run 41082170356 passed (all three feeds readable, four findings as predicted, bodies had literal `\n`, fixed in `012cf7c` by a schema validator and a prompt line); first live run 2026-10-31 06:00 UTC created 118 to 121.

Observations from the 107 dry run worth keeping:
- Research hit the latency guard after three ESPN fetches; implement fetched the Liga Portugal site, `bootstrap-static/` and API-Football itself. That is the intended split, and the budgets held.
- `bootstrap-static/` carries `elements[].in_dreamteam` directly, so `event/<n>/live/` was never fetched. Fine for 107's criteria.
- 3.8-flash accepted 24 requests in one day against the documented 20, so the per-model limits are approximate.
- Honeycomb's `gen_ai.usage.input_tokens` sum for research (1.05M) is about three times `result.json` (366k). The harness limit is enforced on the smaller number; the two count differently and nobody has reconciled them yet.

## What this is

An autonomous software factory: background agents that triage issues, plan and build features, fix bugs, review and merge PRs, verify deploys, and turn production errors into bug issues. It targets one repository per deployment, configured in `factory.config.json` (currently `nimeshjm/fantasy-football`). The design follows the Horthy/Osmani factory model and the constraints in the author's blog series (deterministic guards, plan before code, small diffs, agent-plus-human review split, lights-off autonomy earned per loop).

Stack, decided by the owner: Strands Agents (Python) on the Gemini API free tier; GitHub Actions as orchestration and sandbox; a free-tier Cloudflare Worker as control plane; Honeycomb via OpenTelemetry. Not Claude Code.

## Where things are

| Location | What |
|---|---|
| `/Users/admin/repos/software-factory` | this repo, the factory, public at `nimeshjm/software-factory` |
| `docs/three-amigos.md` | the human/agent boundary: roles, gates, where each rule lives in code |
| `/Users/admin/repos/fantasy-football` | the target, local clone, on `main` |
| `CONTRACTS.md` | the shared shapes every part must honour; change it first |
| `docs/history.md` | the build and graduation log, setup steps and the Plan A design, trimmed from this file on 2026-10-29 |
| `README.md`, `scripts/README.md`, `control-plane/README.md`, `agents/README.md` | runbooks per area |
| `.env` (git-ignored) | local copies of every secret and known value |
| `https://factory-control-plane.nimeshjm.workers.dev/status` | control plane health: quota, in-flight runs, last tick's decisions (no token) |
| `~/.claude/plans/i-want-to-build-moonlit-beacon.md`, `~/.claude/plans/make-the-fantasy-application-keen-puddle.md` | original build and target-agnostic refactor plans |

## Repo layout

- `factory/` Python harness:
  - `run.py` is the entrypoint.
  - `gemini.py` is the Gemini model. It retries 503s and falls back to the next model on a daily-quota 429.
  - `guards/` holds the tool, quota, latency, context and output guards, plus `tool_log.py`, which records every tool call.
  - `tools/` holds the workspace-scoped file, shell, checks and git tools, plus Honeycomb, `fetch.py` and `web_search.py`.
  - `policy/` covers autonomy, merge, the author gate and `findings.py` (the spike checks).
  - `schemas/` holds the proposal models and the `TargetProfile`.
  - `prompts.py` renders `{{target_profile}}`.
- `agents/` system prompts per agent and phase, plus the split review template.
- `scripts/` the only code that mutates GitHub (`apply-proposal.sh`), plus input gathering, deploy polling, Honeycomb queries, label sync (run by CI) and the config-sync check.
- `.github/workflows/` `agent-run.yml` (reusable harness), six `workflow_dispatch` wrappers, `ci.yml`.
- `control-plane/` Cloudflare Worker: webhooks, cron pre-flight, ledger, kill switch, GitHub App auth.
- `smoke/` Playwright HTTP smoke test driven by the profile.
- `examples/fantasy-football/` the onboarding overlay for the current target. `templates/target/` the generic starter for a new target (`ONBOARDING.md` has the steps).

## Core rules (enforced in code, not prose)

1. **Agents propose, pipelines act.** No agent process holds a GitHub, Cloudflare or smoke token. Each agent emits a schema-validated `proposal.json`; `scripts/apply-proposal.sh` applies it with a freshly minted App token.
2. **The target profile can only add.** The profile is `factory.yml` in the target, read from its default branch only. It can only add to the compiled-in baseline deny-list, secret patterns and never-export env list. `factory.yml` and `.github/**` are always lit, and agents cannot edit them.
3. **Default lit.** Only PRs confined to `dark_paths`, or dependency-only Dependabot bumps when `dark_dependency_bumps` is on, merge without a human. Everything else waits for a human `APPROVED` review; the reviewer agent never merges those itself.
4. **Every guard emits a signal.** `result.json` always exists with a `stop_reason`; the control plane marks runs that never report as `lost` and alerts.
5. **Quota is the budget.** Each run has turn and token limits and a model-call cap, and the control plane reserves calls against a daily factory-wide ceiling.

## State of the work

**Running live.** The control plane ticks every 15 minutes. Each tick dispatches:
- triage for unlabelled issues;
- the reviewer, hourly, for the oldest open Dependabot PR;
- the builder for `status:backlog` features and enhancements;
- the bug-fixer for backlog bugs;
- the incident responder once a day at 06:00 UTC.

`POST /admin/pause` stops all of it. The control plane alerts to Discord (`ALERT_WEBHOOK_URL`) on failed, timed-out or lost runs and on tick or dispatch failures. CI runs tests and lint and syncs labels to the target on every push to `main`. It deploys the Worker only when the push changes `control-plane/**` or `ci.yml`.

**Graduation** (each agent dry-run first, then live):

| Agent | State |
|---|---|
| triage | live: issue 35 (children 100 to 103), epic 106 (107 to 110), epic 115 (116, 117) |
| reviewer | live: Dependabot PR 96 (lit, no merge); builder PR 105 (dark, squash-merged) |
| verifier | live: deploy identity plus smoke after PR 105 |
| builder (code) | live: issue 104 went to PR 105, auto-merged dark, verified and closed with no human |
| builder (spikes) | in progress: see below |
| bug-fixer | not yet run (has web research) |
| incident responder | deferred: Honeycomb reads need Enterprise (see Known gaps) |
| security-triage | live: daily at 06:00 UTC since 2026-10-31; created issues 118 to 121; two follow-ups open (see Do next) |

**Web research and spikes** (details in `CONTRACTS.md`, `## Web research (web_search, fetch_url) and spikes`):
- **Tools:** the builder and the bug-fixer have `web_search` (Tavily free tier) and `fetch_url` in research and implement. fantasy-football's `web.allowed_hosts: ["*"]` allows any public https host.
- **`fetch_url` output:** JSON is read up to 2 MB and starts with an indented key tree of the whole body. Every cut is marked `[clipped: ...]`, and the result fits the context guard's 20,000 chars.
- **Spike delivery:** a builder issue labelled `spike` delivers `findings_md` as a `<!-- factory:findings -->` issue comment, then sets `status:done` and closes the issue, with no PR. Apply posts only if:
  1. at least one fetch got an HTTP response;
  2. `check-findings` matches every "HTTP status" claimed in a section to a fetch of that section's URL.

  It then appends the pipeline's `## Evidence` list of every fetch and search.
- **Spike models** (since `cb52a4f`):
  - research uses `models.spike_research`: `gemini-3.8-flash`, then `gemini-3.6-flash`, then `gemini-3-flash-preview`, capped at 25 calls. It fetches every source and records per-criterion evidence (URL, status, key paths, samples) in `research_md`.
  - implement uses `models.spike`: `gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite`, capped at 15 calls and 300k tokens. It writes the findings from that evidence and fetches only gaps.
- **Model fallback:** each `models.<key>` may list `fallback_model_ids`. A 429 whose quota is per day switches the run to the next model at once; per-minute 429s keep the backoff. Each switch shows as `[model] ...` in the log, the `factory.model_switch` span event, and `result.json` `model_ids_used`. Strands yields `messageStart` before reading the stream, and the 429 only surfaces on that read, so the wrapper holds `messageStart` back. Verified in CI on 2026-10-29: 3.8 → 3.6 → 3 Flash.
- **Tool-call evidence:** `result.json` `tool_calls`, a `[tool] ...` line per call in the step log, and `factory.tool_call` span events in Honeycomb.

**Issue 107 (first spike, part of epic 106 "player awards") history.** What failed, and the fix each failure produced:

| Run | Outcome | Fix that followed |
|---|---|---|
| 40904690257 | findings written from memory | at least one fetch required |
| 40908969502 | claimed statuses it never got | `check-findings` |
| 40912644032 | missed `in_dreamteam` | the tool had cut the 1.1 MB body; JSON key tree added |
| 40916924033 | flash-lite ignored fields the tool showed it | a stronger spike model |
| 40951538447 | good dry run (implement on flash, 27 calls, 533k tokens) | none needed |
| 40958393669 (live) | implement re-fetched research's sources, called `retrieve_context` 8 times, wandered off target, stopped at 918k tokens | the `cb52a4f` model swap |
| 41053297498 (dry, 2026-10-30) | good: 3.8-flash research 22 calls, flash-lite implement 9 calls, findings verified by hand | none needed; live run held for Monday |
| 43044016537 (bug-fixer, live, 2026-11-11, issue 118) | planned, fixed and pushed `fix/118-js-stack-trace-exposure-information` (two files, as planned); `gh pr create` got a 504 and the run failed, leaving 118 `status:in-progress` with no PR | `gh_retry` / `create_pr` in `scripts/lib.sh` (retries 5xx, reuses a PR the 5xx may have created); branch kept as `backup/118-attempt1-504` |
| 43051531921 (bug-fixer, live, 2026-11-11, rerun) | **good**: first live bug-fixer run end to end; PR 126 opened, 118 `status:in-review` | none needed |
| 43225593828, 43227971800, 43237920290, 43243138668, 43245862580 (live, 2026-11-12, 13:15 to 15:15) | all died in research on `gemini-3.8-flash` before any tool call: hard timeout, `ReadError`, `Deadline expired`, `high demand`; zero tokens; 107 stayed `status:backlog` and the tick retried every 15 minutes | unresponsive-model fallback in `factory/gemini.py`; per-issue failure backoff in `preflight.ts` (see Do next) |

**Open issues on fantasy-football:**
- **107** spike, `status:backlog`, nothing posted. Released 2026-11-11; five live runs on 2026-11-12 died in research on a throttled 3.8-flash (see Do next). It is the first candidate again once the failure cap lifts or a human dispatches it.
- **118** closed `status:done` 2026-11-12 (PR 126). **119 to 121** security bugs, `needs-human`, yours (Dependabot bumps for `undici` and `@vitest/mocker`; `sharp` has no PR).
- **35 and 106** epics, planned 2026-11-12, `status:plan-review needs-human`: read the plan comment, then `plan:approved` or relabel. **115** epic, `status:backlog risk:high`, will be planned next.
- **117** "Spike other football API providers", `spike risk:low`, held with `needs-human`.
- **116** "Remove API-Football", `risk:high size:S`, gets a plan run when it reaches the front of the queue, then waits for `plan:approved`.
- **125** "Liga Portugal Dream Team page as best XI source", `risk:low size:S`, `status:in-review` with **PR 129** (run 43264835535, 2026-11-12 17:11): draft, CI red (tests nested inside an `it`), reviewer asked for changes. Revised by the factory on 2026-11-13 18:01 (test nesting fixed), reviewer approved at 18:04; waits for the owner's `APPROVED` review.
- **108 to 110** awards implementation (epic 106) and **100 to 103** (epic 35) are `author-not-trusted` (bot-filed, no `factory:accepted`).

**Verification of the Plan B working tree (2026-10-30, uncommitted at the time of writing):**

| Check | Result |
|---|---|
| `uv run pytest -q` | 193 passed |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 30 passed, clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| `gather-inputs.sh security` and `apply_security` with a stub `gh` | shapes, skip rules, `needs-human`, priority override, `open_pr` match and `resolved` path behave as the contract says |
| CI on `main` (`5571786`) | green on all jobs |

**Setup is complete:**
- **GitHub App** `software-factory-agent-bot` (id 5514689), installed on both repos.
- **Repo secrets:** `GEMINI_API_KEY` (dedicated Google Cloud project `gen-lang-client-0040576374`), `FACTORY_APP_ID`, `FACTORY_APP_PRIVATE_KEY`, `HONEYCOMB_API_KEY`, `HONEYCOMB_QUERY_KEY` (unused), `SMOKE_AUTH_TOKEN`, `FACTORY_TOKEN`, `TAVILY_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- **Repo variables:** `FACTORY_URL`, `TARGET_BASE_URL`.
- **Worker:** secret `ALERT_WEBHOOK_URL` points at Discord; the target webhook (id 704460903) is registered for `issues, pull_request, check_suite, workflow_run, code_scanning_alert, dependabot_alert`. `pull_request_review` was added on 2026-11-12 for the approval trigger.
- **Target:** has the label set, squash-only merges and a `main` ruleset.
- **To rotate the Cloudflare token:** put it in `.env`, then run `sed -n 's/^CLOUDFLARE_API_TOKEN=//p' .env | tr -d '\r\n"' | gh secret set CLOUDFLARE_API_TOKEN`.

## Known gaps and judgement calls

**Gemini free tier and budget**
- **Limits per project and per model** (AI Studio rate-limit page, 2026-10-29):

  | Models | Per minute | Tokens per minute | Per day |
  |---|---|---|---|
  | `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3-flash-preview` | 5 | 250K | 20 each |
  | `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | 15 | 250K | 500 each |

  - `gemini-3.5-flash` and `gemini-3.7-flash` are aliases for 3.6 and 3.8 Flash, sharing their buckets, so they are never listed.
  - 2.5 models return 404 for new users.
  - The Pro models and Gemini 4 Argon are 0 on the free tier.
  - Gemma 4 allows 16K tokens a minute, which is less than one request.
  - flash-lite throttles so far are per-minute token limits, not daily ones.
  - The 400/day in `factory.config.json` is the factory's own ceiling, not a Google limit.
- **Flash budget:** 60 flash requests a day across three models, so about two spikes a day. When every model on a list is exhausted the run fails; since the salvage step (2026-11-12) a failed builder or bug-fixer run is reset to `status:backlog` by the pipeline. A switch can finish a run on an older model. The control plane doesn't track exhaustion, so each run spends one request per exhausted model to find out.
- **Calls the cap doesn't see:**
  - Throttle and 503 retries count against a run's model-call cap, so a 429 storm or a long outage can end a run early.
  - Strands' summarisation calls are not counted, so ledger counts can under-report slightly.
- **Other free providers**, if flash runs out (third-party figures, 2026-10-29):
  - Candidates: Mistral's free tier (about 1B tokens a month, prompts may train models, native Strands provider) and NVIDIA NIM (`gpt-oss-120b`, OpenAI-compatible).
  - Ruled out: Groq (8K tokens a minute) and OpenRouter free (50 requests a day).
  - Any switch needs a model adapter beside `factory/gemini.py`.

**Spikes and web research**
- **Checks are textual.** `check-findings` ties claimed statuses to fetches; nothing ties a named field to fetched content. Read a spike's findings before acting on them.
- **Search budget:** 10 searches and 20 fetches per phase. Tavily's free tier allows about 1,000 searches a month.
- **Big JSON:** bodies over 2 MB get no key tree. Very wide JSON drops samples and can still fill the 20,000 chars.
- **Prompt injection:** with `["*"]`, an injected page could get data into a URL or search query; only the prompts forbid it. The agent holds no GitHub token and the repo is public. DNS rebinding between the address check and the connect is not prevented.
- **Stuck runs:** until 2026-11-12 any failed builder or bug-fixer implement run left its issue `status:in-progress` for a human to reset. The salvage post-step (`scripts/salvage-run.sh`) now resets it, pushes the work to `backup/<N>-attempt<k>-<stop_reason>` and comments `<!-- factory:failure -->`; a run that dies before apply never set the label in the first place. Runs cancelled or lost mid-apply can still leave it; the dashboard's `stuck-in-progress` row covers that.

**Merge and review**
- **The reviewer does not notice deleted tests.** PR 126 (bug-fixer rerun on 118) removed the existing "rate limits back-to-back probes" test and added none for the new error path, yet `coverage` and `plan_drift` passed and the verdict was approve. The first attempt (`backup/118-attempt1-504`) had the right test file. Check test diffs by hand on `lit` PRs; a deterministic check (fail `coverage` when a `test/**` diff removes more `it(` blocks than it adds) would catch it.
- **Dependabot:** at most one merge per reviewer run, cron-triggered only. `wrangler` and `@cloudflare/*` bumps are always lit (`lit_dependencies`). The merge-policy trigger is inferred from the PR author.
- **Review debounce:** reviews are debounced per PR for 5 minutes, so a push during an in-flight review may leave that review covering the older commit. A human approval (`pull_request_review` `submitted`, since 2026-11-12) skips that window and only waits for an in-flight review of the same PR.
- **Approved lit PRs used to sit:** until 2026-11-12 nothing re-ran the reviewer after a human approval, so PR 126 stayed open with two approvals, green checks and a resolved thread while `bugs_first` kept the builder off 107 and 125. The control plane now maps approvals to a reviewer run; the hook still needs the event added (Do next).

**Platform**
- **Honeycomb reads:** stays `enabled: false` in the target profile. The Query Data API is Enterprise-only, so liveness and error polling can't work on the free plan; the verifier falls back to smoke checks and the incident responder emits nothing. Ingest works (EU region). To restore reads, port `scripts/honeycomb-query.sh` and `factory/tools/honeycomb.py` to the Honeycomb MCP server (all plans).
- **Config mirroring:** the control plane mirrors agent limits in `wrangler.jsonc` and `src/config.ts`, and `scripts/check-config-sync.sh` catches drift. Its tick must stay under the free plan's 50 subrequests.
- **Example profile secret rule:** it treats `FANTASY_(EMAIL|PASSWORD|SESSION_COOKIE)=value` as a leak; bare variable names are allowed.
- **Multi-target** is out of scope; the config shape allows it later.

## Planned work

Plan A (spikes and web research) shipped on 2026-10-29. Plan B (security findings become issues) was built on 2026-10-30 and waits for graduation (see Do next). `CONTRACTS.md` is the reference for both; the Plan B spec is its `## Security triage` section.

### Plan B as built

- **Agent** `security-triage` (`factory.config.json`): model `cheap`, phase `triage`, tools `read_file`, `list_files`, `search_files`, 15 model calls, triage limits. Prompt `agents/security-triage/PROMPT.md`. Proposal `SecurityProposal` (`factory/schemas/models.py`).
- **Gather** `scripts/gather-inputs.sh security` writes `inputs/security.json`: per-feed availability, default-branch code-scanning alerts, Dependabot alerts grouped per package (with `relationship` and a matched open Dependabot PR), secret-scanning counts by type (the secret value is never read), and tracked issues with their current signature or null when the alert is gone.
- **Harness** stops before any model call with `stop_reason: no_inputs` when no feed is readable (`factory/run.py`); the inputs reach the model inside `<untrusted source="security.json">`.
- **Apply** `apply_security` (`scripts/apply-proposal.sh`): marker `<!-- factory:security:<hash> -->`, at most 5 new issues per run, labels `bug security status:backlog priority:N factory:accepted`, priority recomputed from the inputs, `needs-human` for secret scanning, transitive dependencies and `lit_dependencies`, skips for tracked signatures and groups with an open Dependabot PR; `resolved` entries get a comment, `status:done` and a close, only when all three feeds were readable.
- **Control plane:** dispatched on the 06:00 UTC tick (subject `<date>`, beside the incident responder) and on `code_scanning_alert` (`created`, `appeared_in_branch`) and `dependabot_alert` (`created`), debounced to one webhook run per hour.
- **Deviations from the original plan:** Dependabot findings are one per package, not one per advisory (eleven `undici` issues would have hit the cap); `needs-human` for transitive dependencies uses the API's `relationship` field; the signature for `resolved` entries carries the marker hash; the prompt file follows the single-phase `PROMPT.md` convention; the daily window is 06:00, not 06:30, because the tick check is hour-granular.
- **Known gaps:** grouped Dependabot PRs (`dependabot/npm_and_yarn/vitest-<digest>`) are not matched to their alerts, so a package covered by a grouped PR still gets an issue; the bash gather and apply paths have no automated tests (validated with a stub `gh`); `secret_scanning_alert` webhooks are not subscribed.

## Claude Code tooling for working on the factory

For developing this repo (not the factory's runtime agents, which stay Strands + Gemini), the `factory-orchestrate` skill (`.claude/skills/`) drives a change from the main Opus session. It delegates read-only research to the `factory-researcher` subagent (Fable) and scoped work packages to `factory-implementer` (Sonnet) (`.claude/agents/`). Review the implementer's diff yourself: its tests have missed real-API behaviour (the `messageStart` ordering), and one report came back empty. Deploys, workflow dispatches, secrets and target merges stay with the owner.

The `factory-audit` skill (`.claude/skills/factory-audit/`) is the read-only daily check: `collect.sh --hours 24` gathers the window's runs, their `result.json` artifacts, `/status`, and the target's issue and PR movement into one report, and the skill verifies each item of `## Do next` against that evidence and lists the stalls the tick cannot see (an approved `lit` PR nobody merges, an in-progress issue with no run, a lost in-flight run). Added 2026-11-12.

## How to work on it

```sh
uv sync && uv run pytest -q                       # harness
uv run python -m factory.run --agent triage --phase triage --inputs inputs/ --out out/ --fake-model
cd control-plane && npm ci && npm test            # control plane
actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh
```

Change `CONTRACTS.md` before changing any interface; the four areas (harness, prompts, scripts and workflows, control plane) were built against it in parallel and only agree because of it.
