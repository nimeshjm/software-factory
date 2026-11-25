# Handoff: software factory

Rewritten 2026-11-25. The history before that is in `git log` (the 2026-11-23 and 11-24 entries this replaces are in commits `79a15ec` to `2143439`). `CONTRACTS.md` is the reference for every interface named here.

## Do next (2026-11-25; everything is committed and on `origin/main`, the Worker is deployed)

**Owner decisions open**
1. **Merge target PR 139, then send PR 138 back for a revise.** Owner rule (2026-11-25): agents may write migrations; PRs touching them are reviewed by the factory and approved only by a human. PR 139 removes `migrations/**` from the target's `protected.paths` (it stays in `risk.high_paths`, so plans stop for `plan:approved`, and outside `dark_paths`, so every such PR is `autonomy:lit`); `examples/fantasy-football/factory.yml` mirrors it, and also drops `package.json`, matching the target since PR 135. PR 138 (#108) lacks `migrations/0006_player_awards.sql` because the old rule blocked it; after 139 merges: `gh pr edit 138 -R nimeshjm/fantasy-football --remove-label needs-human --add-label revise:requested`. The revise checklist carries the reviewer's failed `acceptance` row (the missing migration).
2. **#109 and #110: rescope or close.** Spike #107 found no source for player of the match or month (only `events[].top_element` and the dream-team flags, used since #125). Rescope them to the player-of-the-week proxy, then add `factory:accepted` (the tick shows them `author-not-trusted` until then). Epic #106 is a tracking issue only; PR 137 is closed (branch kept).
3. **Re-run `HONEYCOMB_CONFIG_KEY=... scripts/honeycomb-board.sh`** once a builder or reviewer run on `9dd65f6` or later has finished: panels 21, 22 and 27 to 29 are skipped until their columns exist (`factory.tool.result_tokens`, `factory.prompt.*`, `factory.task.*`). 30 (cached input) can apply now.
4. **Security bugs #119 (`sharp`) and #120 (`undici`), priority 1:** they re-plan on their own (see Built 2026-11-25); approve the new plans when they post. While they are open without `needs-human` the builder waits (`bugs_first`).
5. **Compare `paid_cost_usd` with AI Studio Spend after a week.** Cache reads are now priced at 10% of input (`cached_input`); prices are third-party listings.

**First live proofs to check on the next runs** (Honeycomb `software-factory` dataset, or the run log)
- *Overload wait:* a 503 logs `[model] <id> overloaded (503); waiting 20s` and retry reason `overload_wait`, not an immediate switch. When every free model is overloaded the run stops `model_overloaded` and never shows `switching to the paid key`; only `paid_key:daily_quota` may.
- *Prompt cuts:* builder implement's first `chat` span under about 11k input tokens (#108 was 14.8k); panel 29 shows no `open issues` section for implement, plan, revise or review; panel 27 `task_tokens` near 3.5k for builder implement.
- *Earlier 11-24 changes, still unproven live:* `[edit_guard]` nudges on a revise that reads without editing; `read_context` in `factory.tool_call` names; tool calls above model calls in panel 24 (batched reads); `[model] <id> throttled (per minute); waiting <n>s` with no switch.

**Built 2026-11-25 (`CONTRACTS.md` Plan hold, Tick candidates)**
- *Empty plans.* #119 and #120 failed implement twice with `no_changes`: their approved plans dated from when `package.json` was protected and listed no files. Now a plan with no files (not a spike) holds with `empty_plan` and `needs-human`; an already approved plan with no files is planned again instead of implemented; and any held plan removes `plan:approved`, so a new plan always needs a new approval. #119 and #120 need no relabel: after their backoff the next tick re-plans them, and the new plans stop for your approval.
- *Overloaded reviews retried.* The tick re-dispatches a factory-PR review that stopped `model_overloaded`, 60 minutes later, at most 3 times in 26 h.

**Gaps known and not built**
- `model_overloaded` failures count toward the failure cap (3 in 24 h) like any failure.
- Unverified: a mid-phase switch between Gemini 3 and Gemini 4 models (thought signatures) on design and review; 3.8 Flash's real free requests per day (20 per the spike notes).

## What changed on 2026-11-24 (one line each; details in `CONTRACTS.md`)

- **Two keys, daily paid budget.** `GEMINI_FREE_API_KEY` (a project with billing off) is walked through the whole model chain first, then `GEMINI_API_KEY` (billing-linked, prepay, GBP 6 monthly spend cap set in Google) on its own cheaper `paid_model_ids` chain. The control plane hands each run a paid allowance (`DAILY_PAID_CAP_USD` 0.5, `RUN_PAID_CAP_USD` 0.3) and stops dispatch once it is spent (`paid_budget`). CONTRACTS Key tiers, Daily paid budget.
- **Only a daily-quota 429 moves a run onto the paid key.** Overload, per-minute throttles, stalls and dropped connections walk the free chain only, then stop `model_overloaded` (retried after the 60/180 min backoff; a revise keeps its labels). Why: on 11-24 at 12:32 and 12:44 Google answered the free key with 503 "high demand" on every model and two runs went paid within 70 s ($0.16).
- **Waits before switching models.** Per-minute 429: the 429's `retryDelay` + 1 s, up to 3 times, capped at 65 s. 503: 20, 40, 60 s. Only then does a failure count toward `unresponsive_switch_after` (2).
- **Billing block.** A 402 or a spend-cap 429 stops the run `billing_blocked`; with a free key the control plane blocks the paid key for the month (`paid_blocked_month`), otherwise it pauses the factory. `/admin/resume` clears both.
- **Model routing.** Design (research, plan) and review: `gemini-3.8-flash` -> Hydrogen -> 3.5 Flash-Lite, `high` thinking. Build: 3.5 Flash-Lite -> 3.1 Flash-Lite -> Hydrogen, `low` thinking. Paid chains are the flash-lite pair. Hydrogen's free tier is 20 requests a day, so it is always last.
- **Revise edits early.** `EditGuard` nudges after 12 reads or 35% of the token budget without an edit; revise opens with a `## What to fix` checklist built from the previous review's fail rows and failing CI. A failed revise is backed up to `backup/<N>-revise-attempt<k>-<stop_reason>`; the PR branch is never pushed.
- **Fewer input tokens.** Reads batched into one turn (all 13 prompts); only `conventions.md` inlined (revise adds `architecture.md`), other context files listed and read with `read_context`; tool results older than the last 6 shrink to a 120-token head and tail; lean phases (builder and bug-fixer plan, implement, revise; reviewer) drop the open issue and PR lists and the profile-covered `factory.json` keys; the plan is inlined once.
- **Cost on the dashboard and in Honeycomb.** Dashboard: `Paid spend today` tile, costs panel, paid column per run. Board panels 21 to 30: tool result sizes, prompt size, batching, re-reads, paid spend, prompt makeup (`PromptStats` hook: `factory.prompt` per request, `factory.task_section` once per phase), input by request number, largest prompt sections, cached input.

**What a call costs, measured 11-24 (#108 builder implement):** the first call was 14.8k input tokens and grew only to about 25k by call 27, so the fixed prompt dominated: about 7k system prompt plus tool definitions (`agents/builder/implement.md` alone is 12.1k chars) and about 7.5k task message, of which the open issue list was 39%, raw `factory.json` 11% and a duplicated plan 12% (all three now cut). Gemini's implicit cache served 80 to 90% of most calls' input. Next candidates if calls are still large: trim `implement.md` and the other system prompts, and per-phase `phase_context_files` for implement and review.

**Honeycomb MCP for Claude Code:** server `honeycomb-eu` (`https://mcp.eu1.honeycomb.io/mcp`, OAuth, local config for this project) is connected; team `nimeshjm-personal`, environment `software-factory`, datasets `factory` and `software-factory`. The Honeycomb plugin's built-in server points at the US region and is disabled for this project. `usage.total_tokens` on `run.finished` is only populated from 2026-11-22; use panel `12-tokens` (spans) for token history.

## What this is

An autonomous software factory: background agents that triage issues, plan and build features, fix bugs, review and merge PRs, verify deploys, and turn production errors into bug issues. It targets one repository per deployment, configured in `factory.config.json` (currently `nimeshjm/fantasy-football`). The design follows the Horthy/Osmani factory model and the constraints in the author's blog series (deterministic guards, plan before code, small diffs, agent-plus-human review split, lights-off autonomy earned per loop).

Stack, decided by the owner: Strands Agents (Python) on the Gemini API (a free-tier key first, then a billing-linked key under a daily budget); GitHub Actions as orchestration and sandbox; a free-tier Cloudflare Worker as control plane; Honeycomb via OpenTelemetry. Not Claude Code.

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
| security-triage | live: daily at 06:00 UTC since 2026-10-31; created issues 118 to 121; 118 and 121 fixed, 119 and 120 open (see Do next) |

**Web research and spikes** (details in `CONTRACTS.md`, `## Web research (web_search, fetch_url) and spikes`):
- **Tools:** the builder and the bug-fixer have `web_search` (Tavily free tier) and `fetch_url` in research and implement. fantasy-football's `web.allowed_hosts: ["*"]` allows any public https host.
- **`fetch_url` output:** JSON is read up to 2 MB and starts with an indented key tree of the whole body. Every cut is marked `[clipped: ...]`, and the result fits the context guard's 20,000 chars.
- **Spike delivery:** a builder issue labelled `spike` delivers `findings_md` as a `<!-- factory:findings -->` issue comment, then sets `status:done` and closes the issue, with no PR. Apply posts only if:
  1. at least one fetch got an HTTP response;
  2. `check-findings` matches every "HTTP status" claimed in a section to a fetch of that section's URL.

  It then appends the pipeline's `## Evidence` list of every fetch and search.
- **Spike models** (since `cb52a4f`):
  - research uses `models.spike_research`: `gemini-3.8-flash`, then `gemini-4-hydrogen`, then `gemini-3.5-flash-lite`, capped at 25 calls. It fetches every source and records per-criterion evidence (URL, status, key paths, samples) in `research_md`.
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
| 43225593828, 43227971800, 43237920290, 43243138668, 43245862580 (live, 2026-11-12, 13:15 to 15:15) | all died in research on `gemini-3.8-flash` before any tool call: hard timeout, `ReadError`, `Deadline expired`, `high demand`; zero tokens; 107 stayed `status:backlog` and the tick retried every 15 minutes | unresponsive-model fallback in `factory/gemini.py`; per-issue failure backoff in `preflight.ts` |

**Open issues on fantasy-football (checked 2026-11-22 15:30 UTC; 16 open):**
- **130** "Dream Team endpoint: fix the response shape, boolean in_dreamteam flag and add the gameweek 11 fixture", `bug priority:2 risk:low size:S status:in-review` with **PR 131** (bug-fixer run 43499894358, 2026-11-13): CI green, reviewer approved; waits for the owner's `APPROVED` review. Blocks the builder (`bugs_first`) until it closes.
- **119 to 121** security bugs, `needs-human`, yours (`sharp`, `undici`, `@vitest/mocker`).
- **35, 106 and 115** epics, `status:plan-review needs-human`: read the plan comment, then `plan:approved` or relabel.
- **116** "Remove API-Football", `risk:high size:S status:plan-review`, waits for `plan:approved`.
- **117** "Spike other football API providers", `spike risk:low`, held with `needs-human`.
- **108 to 110** (epic 106) and **100 to 103** (epic 35) are `author-not-trusted` (bot-filed, no `factory:accepted`).
- Closed since the last check: **107** (spike findings, 2026-11-13), **125** (PR 129, 2026-11-13), **118** (PR 126, 2026-11-12).

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
- **Free-tier limits** (per project and model, AI Studio 2026-10-29, re-checked 11-24): flash-lite models 15 a minute and about 500 a day; 3.8 Flash and Hydrogen 20 a day; Pro and Argon 0. The 400 a day in `factory.config.json` is the factory's own ceiling.
- **Paid key:** billing-linked project, prepay credits, GBP 6 monthly spend cap in Google, $0.50 a day and $0.30 a run in the control plane; cached input at 10% of the input price. See Do next for what is unproven.
- **Calls the cap doesn't see:** strands' summarisation calls; overload and throttle waits count as `throttles`, not calls.
- **Other free providers**, if Gemini's free tier dries up (third-party figures, 2026-10-29): Mistral's free tier (about 1B tokens a month, prompts may train models, native Strands provider) and NVIDIA NIM (`gpt-oss-120b`). Groq and OpenRouter free are too small. Any switch needs a model adapter beside `factory/gemini.py`.

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

Plan A (spikes and web research) shipped on 2026-10-29. Plan B (security findings become issues) was built on 2026-10-30 and has run live daily since 2026-10-31. `CONTRACTS.md` is the reference for both; the Plan B spec is its `## Security triage` section.

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
