# Handoff: software factory

Written 2026-10-28, updated 2026-10-29 21:30 UTC (JSON fetch shape, spike model split, issue 107 clean dry run, issue 115). Everything below is the state at that time.

## What this is

An autonomous software factory: background agents that triage issues, plan and build features, fix bugs, review and merge PRs, verify deploys, and turn production errors into bug issues. It targets one repository per deployment, configured in `factory.config.json` (currently `nimeshjm/fantasy-football`). The design follows the Horthy/Osmani factory model and the constraints in the author's blog series (deterministic guards, plan before code, small diffs, agent-plus-human review split, lights-off autonomy earned per loop).

Stack, decided by the owner: Strands Agents (Python) on the Gemini API free tier; GitHub Actions as orchestration and sandbox; a free-tier Cloudflare Worker as control plane; Honeycomb via OpenTelemetry. Not Claude Code.

## Where things are

| Location | What |
|---|---|
| `/Users/admin/repos/software-factory` | this repo, the factory |
| `/Users/admin/repos/fantasy-football` | the target, local clone, on `main` |
| `~/.claude/plans/i-want-to-build-moonlit-beacon.md` | plan for the initial build |
| `~/.claude/plans/make-the-fantasy-application-keen-puddle.md` | plan for the target-agnostic refactor |
| `CONTRACTS.md` | the shared shapes every part must honour; change it first |
| `README.md`, `scripts/README.md`, `control-plane/README.md`, `agents/README.md` | runbooks per area |

## Repo layout

- `factory/` Python harness: `run.py` entrypoint, `gemini.py` (Gemini model that retries 503s), `guards/` (tool, quota, latency, context, output, and `tool_log.py` which records every tool call), `tools/` (workspace-scoped file, shell, checks, git, Honeycomb, `fetch.py`, `web_search.py`), `policy/` (autonomy, merge, author gate, `findings.py` spike checks), `schemas/` (proposal models and the `TargetProfile`), `prompts.py` (renders `{{target_profile}}`).
- `agents/` system prompts per agent and phase, plus the split review template.
- `scripts/` the only code that mutates GitHub (`apply-proposal.sh`), input gathering, deploy polling, Honeycomb queries, label sync (run by CI), config-sync check.
- `.github/workflows/` `agent-run.yml` (reusable harness), six `workflow_dispatch` wrappers, `ci.yml`.
- `control-plane/` Cloudflare Worker: webhooks, cron pre-flight, ledger, kill switch, GitHub App auth.
- `smoke/` Playwright HTTP smoke test driven by the profile.
- `examples/fantasy-football/` the onboarding overlay for the current target (applied in PR 99). `templates/target/` the generic starter for a new target.

## Core rules (enforced in code, not prose)

1. Agents propose, pipelines act. No agent process holds a GitHub, Cloudflare or smoke token. Each agent emits a schema-validated `proposal.json`; `scripts/apply-proposal.sh` applies it with a freshly minted App token.
2. The target profile (`factory.yml` in the target, read from its default branch only) can only add to the compiled-in baseline deny-list, secret patterns and never-export env list. `factory.yml` and `.github/**` are always lit and cannot be edited by agents.
3. Default lit. Only PRs confined to `dark_paths` merge without a human. Everything else waits for a human `APPROVED` review; the reviewer agent never merges itself.
4. Every guard emits a signal. `result.json` always exists with a `stop_reason`; the control plane marks runs that never report as `lost` and alerts.
5. Quota is the budget: per-run turn and token limits, a per-run model-call cap, and a daily factory-wide model-call ceiling reserved by the control plane.

## State of the work

**Factory repo: committed, public at `nimeshjm/software-factory`, setup steps 1 to 7 done (2026-10-28 evening).** Secrets and variables are on the repo, the App `software-factory-agent-bot` (id 5514689) is installed on both repos, the target has the label set, squash-only merges and the `main` ruleset, and the control plane is deployed at `https://factory-control-plane.nimeshjm.workers.dev` with the target webhook registered. Local copies of every secret and known value live in `.env` (git-ignored); `FACTORY_URL` is set there and as a repo variable.

Step 8 progress (as of 2026-10-28 22:20 UTC): triage graduated (dry run, then live on issue 35: labels applied, child issues 100 to 103 created; live on 92, which it judged already implemented and the owner closed). Reviewer graduated (dry run, then live on Dependabot PR 96: review posted, `autonomy:lit` applied, no merge, as the policy requires a human approval for dependency bumps). Verifier dry run passed (deploy identity matched, smoke passed, liveness reported disabled). Builder dry run passed all three phases on issue 104, a README-only `risk:low size:S` issue filed for this purpose; the owner then added `status:backlog` so the cron runs it live. The cron's live builder run then completed the whole loop with no human: PR 105 opened 22:32 UTC, the webhook-dispatched reviewer labelled it `autonomy:dark` and squash-merged it at 22:34, the deploy triggered the verifier, which passed (identity, smoke) at 22:35, and issue 104 closed as `status:done`. Builder, reviewer and verifier are graduated live. Not yet run: bug-fixer. Incident responder is deferred (see Honeycomb below).

Later on 2026-10-28 (23:00 to 23:40 UTC): triage ran live on epic 106 (children 107 to 110). The author gate rejected every triage-created child (App bot is `CONTRIBUTOR`), fixed by inheriting `factory:accepted` from a trusted parent; tick skip reasons now list every blocked issue; `GET /status` is documented in `control-plane/README.md`; CI deploys the Worker on push to `main`; `dark_dependency_bumps` lets dependency-only Dependabot bumps merge on green CI (factory side pushed; target side in fantasy-football PR 113, merged 2026-10-28 23:23 UTC). The builder ran live on 107 (a research spike) and failed at apply with "no commits ahead of main": the agent correctly proposed no files, but the only delivery path is a PR. Issue 107 was then stuck at `status:in-progress`; it was reset on 2026-10-29 (see below).

**The control plane is live** (resumed 22:00 UTC after a pause during graduation). It has no per-agent gate: every tick dispatches triage for unlabeled issues, the reviewer hourly for the oldest open Dependabot PR, the builder for `status:backlog` features and enhancements, the bug-fixer for backlog bugs, and the incident responder once a day at 06:00 UTC. `POST /admin/pause` stops all of it; `GET /status` shows quota and the last tick's decisions.

**2026-10-29: web research, spikes and evidence (all pushed; last commit `3c24027`, CI green).**

- **Web research.** The builder and the bug-fixer have `web_search` (Tavily free tier) and `fetch_url` in their `research` and `implement` phases, on every issue. The target profile's `web.allowed_hosts` switches both on; fantasy-football has `["*"]` (any public https host; target PR 114, merged). `TAVILY_API_KEY` is set on this repo. Details and rules: `CONTRACTS.md`, `## Web research (web_search, fetch_url) and spikes`.
- **Spikes.** A builder issue labelled `spike` (investigation only) delivers `findings_md` as a `<!-- factory:findings -->` issue comment, sets `status:done` and closes the issue, with no PR. Apply only posts findings that pass three checks: at least one `fetch_url` with an HTTP response; `check-findings` (every "HTTP status" claimed in a section must match a fetch of a URL in that section); and then it appends a pipeline-built `## Evidence` list of every fetch and search. Builder research and implement on spike issues use `models.spike` (flash-lite, `thinking_level: high`).
- **Tool-call evidence.** `result.json` has `tool_calls` (name, target, status, http_status), the step log prints a `[tool] ...` line per call, and each call is a `factory.tool_call` span event in Honeycomb (environment `software-factory`, dataset `software-factory`; filter `factory.run_id = gh-<run id>-1`). Dry runs now pass `--dry-run`, so they show `dry_run: true`.
- **Reliability and alerts.** Gemini `503 UNAVAILABLE` is retried with backoff (`factory/gemini.py`; it killed two reviewer runs on Dependabot PR 111). The control plane alerts to Discord (`ALERT_WEBHOOK_URL`, confirmed working by the owner) on failed or timed-out runs, lost runs, and tick or dispatch failures. Denied runs are not alerted.
- **Labels** sync to the target on every push to `main` (`sync-labels` job in `ci.yml`); `spike` exists on fantasy-football.
- **Onboarding guide** (`templates/target/ONBOARDING.md`) now has the right App slug and the factory-side steps (target config and its `wrangler.jsonc` mirror, secrets, push, webhook, dry-run graduation).

**2026-10-29 evening: spikes made reliable (pushed; last commit `72cd980`, CI run 40952612166 green, Worker deployed).**

- **`fetch_url` sees whole JSON bodies.** Run 40912644032 showed why the 107 findings missed the fantasy dream-team fields: `bootstrap-static/` is 1.1 MB, and the tool passed only its first 20,000 chars (`elements`, with `in_dreamteam`, starts at char 36,441). JSON responses are now read up to 2 MB and start with an indented key tree of the whole body (types, short samples, array lengths, union of the keys of the first 50 items, repeated subtrees collapsed to "same keys as ..."), then the raw text. Every cut is marked `[clipped: ...]`, and the whole result fits in the context guard's 20,000 chars so the guard no longer cuts its middle. Details: `CONTRACTS.md`, `fetch_url`.
- **Spike prompts.** Findings have one section per acceptance criterion, fetched from the endpoint that criterion names, listing key paths seen in this run's fetches; a field is "absent" only when the result covered the whole body. Ids may come from an earlier fetch (a finished match from a scoreboard).
- **Budget.** Builder and bug-fixer `research` now allow 600k tokens (run 40915023768 died at the old 300k after file reads plus fetches).
- **Spike model split.** flash-lite still ignored fields the tool showed it (run 40916924033). Spike `implement` now runs `models.spike` (then `gemini-3.5-flash`, now `gemini-3.8-flash` with fallbacks, see below; `thinking_level: high`); spike `research` runs `models.spike_research` = flash-lite. Honeycomb confirmed the split on run 40951538447.
- **Daily-quota model fallback (2026-10-29, late).** Each `models.<key>` may list `fallback_model_ids`. A 429 whose QuotaFailure is per-day switches the run to the next model at once (stderr `[model] ... switching to ...`, span event `factory.model_switch`, `result.json` `model_ids_used`); per-minute 429s keep the backoff. Lists: lite tiers `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`; spike implement `gemini-3.8-flash` → `gemini-3.6-flash` → `gemini-3-flash-preview`, capped at 50 model calls. Gotcha found live: strands yields `messageStart` before reading the stream and the 429 surfaces on that read, so the wrapper holds `messageStart` back until content arrives. Verified live: 3.6 Flash (exhausted) switched to 3 Flash and answered.

**Issue 107 (the first spike): clean dry run, awaiting the owner.** It is still `status:backlog` + `needs-human` + `spike`, so the cron skips it. Dry runs:

| Run | Result |
|---|---|
| 40900660253 | stopped after plan: plan set `needs_human` (prompt fixed) |
| 40904690257 | findings written from memory (evidence check added) |
| 40908969502 | claimed statuses it never got (claim check and spike model added) |
| 40912644032 | passed checks; missed `in_dreamteam` (tool clipped the body) |
| 40915023768 | research hit 300k tokens (cap raised) |
| 40916924033 | passed checks; flash-lite ignored `in_dreamteam`, false "no Primeira Liga coverage" claim |
| 40951538447 | **good**: `check-findings` ok; found `elements[].stats.in_dreamteam` (`event/<n>/live/`) and `events[].top_element_info`; ESPN `summary` on a finished `por.1` match; Liga Portugal awards only as HTML news; API-Football 403 without a key. Implement: 27 flash calls, 9 throttle retries, 533k tokens |

Next: remove `needs-human` on 107 so the cron runs it live (a live run makes fresh findings; it must pass the same checks or the run fails and the issue stays `status:in-progress`).

**Issue 115** (filed 2026-10-29 by the owner's session, unlabelled for triage): remove API-Football from fantasy-football (unused at runtime; UEFA rotation already reads ESPN) and spike other football data providers. The `API_FOOTBALL_KEY` line in the target's `factory.yml` must be removed by a human (agents cannot edit `factory.yml`).

Fixes made while graduating, all pushed: issue and PR inputs come from the REST API (`gh --json` has no `authorAssociation`); Google retired the 2.5 Gemini models for new keys, so both tiers use `gemini-3.5-flash(-lite)` with `thinking_level: minimal`; Honeycomb endpoints point at the EU region; the cron passes the oldest Dependabot PR number to the reviewer (a run without `pr` reviews nothing and still spends ~18 model calls). All of these are deployed.

Verification at handoff (2026-10-29, commit `72cd980`, CI run 40952612166 green, all jobs including deploy and label sync):

| Check | Result |
|---|---|
| `uv run pytest -q` | 176 passed |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 27 passed, clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| profile validation on example and template | both valid; empty file rejected |
| deny-list composition | example profile blocks `wrangler` and `EVAL_LIVE`, template only baseline; `git push` and `factory.yml` edits blocked in both |

Exercised live since: the Gemini path, OTLP export to Honeycomb, `gh` mutations for triage, review, merge and verify, the smoke run, webhooks, Tavily search and real fetches (in dry runs). Not yet live: the spike apply branch against GitHub (stub `gh` only), the bug-fixer, the incident responder.

**Target repo: onboarded.** `nimeshjm/fantasy-football` PR 99 (onboarding) and PR 114 (`web.allowed_hosts: ["*"]`) are merged.

## Setup still owed by the owner (in order)

1. Done: pushed, public.
2. Done (PR 99 merged, `GET /version` returns the live SHA). Original note: Merge PR 99 in fantasy-football. After its first deploy, confirm `GET /version` (header `x-dashboard-token`) returns the commit SHA, not `unknown`; the `--var` pass-through in `cloudflare/wrangler-action` is unverified.
3. Done. Create GitHub App `software-factory-agent-bot` (the name `factory-bot` was taken; `factory.app_slug` is updated to match) (Contents RW, Issues RW, Pull requests RW, Actions RW, Checks R, Metadata R, no Workflows). Install on both repos. Convert the key with `openssl pkcs8 -topk8 -nocrypt` for the Worker.
4. Done. Secrets in this repo: `GEMINI_API_KEY` (dedicated Google Cloud project; Google AI Plus does not cover the API), `FACTORY_APP_ID`, `FACTORY_APP_PRIVATE_KEY`, `HONEYCOMB_API_KEY`, `HONEYCOMB_QUERY_KEY`, `SMOKE_AUTH_TOKEN` (the dashboard token), `FACTORY_TOKEN`, `TAVILY_API_KEY` (2026-10-29). Variables: `FACTORY_URL`, `TARGET_BASE_URL`. Worker secret `ALERT_WEBHOOK_URL` points at Discord.
5. Done. Labels: CI's `sync-labels` job runs `scripts/preflight-labels.sh` on every push to `main`. Ruleset on `main` per `templates/target/ONBOARDING.md`.
6. Done. Deploy the control plane (`control-plane/README.md`), set its secrets, register the webhook for `issues, pull_request, check_suite, workflow_run`.
   Since 2026-10-28 23:15 UTC CI redeploys the Worker on push to `main` (`deploy-control-plane` job in `ci.yml`); since 2026-10-29 only when the push changes `control-plane/**` or `ci.yml` (compared against the push's `before` commit; a missing `before` deploys). On 2026-10-29 the owner replaced `CLOUDFLARE_API_TOKEN` with a valid token (`/user/tokens/verify` reports active; current tokens are 53 characters, so length is not a validity check). CI run 40788060878 then deployed the Worker (version `d3dbe4f3-a4b0-403f-9ffa-e2e9bf0ac1bb`). To rotate: put the token in `.env`, then `sed -n 's/^CLOUDFLARE_API_TOKEN=//p' .env | tr -d '\r\n"' | gh secret set CLOUDFLARE_API_TOKEN` (macOS grep has no `-P`).
7. Honeycomb: stays `enabled: false` in the target's `factory.yml`. The read path (`scripts/honeycomb-query.sh`, `factory/tools/honeycomb.py`) uses the Query Data API, which Honeycomb gates to Enterprise; on the free plan a configuration key cannot carry Run Queries, so liveness and error polling cannot work. The verifier falls back to the smoke checks and the incident responder emits an empty list by design. Ingest (factory telemetry via OTLP and control-plane events) works on the free plan and is wired to the EU region. To get the read path back, port it to the Honeycomb MCP server (`honeycomb.mcp_url`, available on all plans) and then revisit the `faas.trigger` liveness filter. The Honeycomb MCP is connected to the owner's Claude Code (2026-10-29) for investigating runs; the factory itself does not use it yet.
8. Graduate loops in order, each first with `dry_run=true` via `gh workflow run <agent>.yml -f dry_run=true ...`: triage, reviewer on Dependabot PRs, verifier, builder on a `risk:low size:S` issue, bug-fixer, incident responder. Done: triage, reviewer, verifier, builder (code). In progress: builder spikes (issue 107 has a clean dry run; owner removes `needs-human` to go live). Not yet: bug-fixer (now with web research), incident responder.

## Known gaps and judgement calls

- Spike checks are textual: `check-findings` only verifies statuses written as "HTTP status" next to a URL in the same section. Claims about fields or auth in prose are not checked; the `## Evidence` list lets a reader see what was actually fetched.
- With `web.allowed_hosts: ["*"]`, a prompt-injected page could get data put into a URL or search query; only the prompts forbid it. The agent holds no GitHub token and reads a public repo, and a leaked Tavily key costs at most the free 1,000 searches a month. DNS rebinding between the address check and the connect is not prevented.
- Spike field claims are prompt-enforced only: `check-findings` ties statuses to fetches but nothing ties a named field to fetched content. Read a spike's findings before acting on them.
- Flash budget: 3 flash models × 20 requests a day. A spike implement phase is about 27 calls and is capped at 50, so roughly two spikes a day fit. When every model in a list is exhausted the run fails on the 429 and leaves its issue `status:in-progress`. A switch can finish a run on an older model (`model_ids_used` shows it). The control plane does not track per-model exhaustion, so each run spends one request per exhausted model to discover it.
- Other free inference providers (looked at 2026-10-29, third-party figures, no quality comparison found): Mistral's free tier (about 1B tokens a month, prompts may train models; native Strands provider) and NVIDIA NIM (`gpt-oss-120b`, OpenAI-compatible) are the candidates if flash runs out; Groq is out (8k tokens a minute is smaller than one request); OpenRouter free is 50 requests a day. Any switch needs a model adapter beside `factory/gemini.py` and its own quota handling.
- JSON bodies over 2 MB get no key tree (`[JSON shape unavailable: body cut ...]`), and a very wide JSON drops samples and can still exceed the 20,000-char result.
- Search budget: 10 searches and 20 fetches per phase; Tavily's free tier is about 50 search-heavy runs a month.
- A Gemini 503 retry counts against the run's model-call cap, so a long outage can still fail a run after about six attempts.
- Dependabot: at most one merge per reviewer run, cron-triggered only. With the profile's `dark_dependency_bumps: true` (set for fantasy-football on 2026-10-28), bumps confined to `package.json` and `package-lock.json` merge on green CI; `wrangler` and `@cloudflare/*` bumps are still always lit via `lit_dependencies`. The merge-policy trigger is inferred from the PR author, not from a real cron signal.
- Reviewer runs are debounced per PR for 5 minutes; a push during an in-flight review is skipped, so that review may cover the older commit.
- Dependency-only PR detection (`toolchain.dependency_files`) only changes merge behaviour when the profile sets `dark_dependency_bumps: true`.
- Any failed builder or bug-fixer implement run (no files and no findings, or spike findings that fail the checks) leaves the issue `status:in-progress`; a human resets it.
- The control plane mirrors agent limits and identity in `wrangler.jsonc` and `src/config.ts`; `scripts/check-config-sync.sh` in CI catches drift. Its tick must stay under the free plan's 50 subrequests.
- `--max-budget`-style spend control does not exist for Gemini; the quota guard counts model calls, including throttle retries, so a 429 storm can exhaust a run's cap early.
- Summarisation inside Strands' context manager makes model calls the quota guard does not see, so ledger call counts can under-report slightly.
- The example profile's secret rule treats `FANTASY_(EMAIL|PASSWORD|SESSION_COOKIE)=value` as a leak; bare variable names are allowed.
- Multi-target is out of scope; the config shape allows it later.
- Gemini free tier, per project and per model (AI Studio rate-limit page, read 2026-10-29): `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3-flash-preview` 5 RPM / 250K TPM / 20 RPD each; `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` 15 RPM / 250K TPM / 500 RPD each. `gemini-3.5-flash` is an alias served as 3.6 Flash and `gemini-3.7-flash` as 3.8 Flash (same buckets, so they are never listed). 2.5 models answer 404 for new users; Pro models and Gemini 4 Argon are 0 on free; Gemma 4 has 14.4K RPD but 16K TPM, below one request. flash-lite throttles seen so far are TPM (peak 301K/250K), not RPD. The 400/day quota in `factory.config.json` is a factory-wide ceiling, not a Google limit.
- `HONEYCOMB_QUERY_KEY` is set as a repo secret but the key cannot run queries (free plan); it is unused while `honeycomb.enabled` is false.

## Planned work (designed 2026-10-28)

Plan A is shipped (2026-10-29) and grew into general web research; a clean spike dry run on 107 is done, going live waits on the owner removing `needs-human` (see State of the work). Plan B is not started. Both plans follow the core rules: agents propose, `apply-proposal.sh` acts; new interfaces go into `CONTRACTS.md` first; each agent graduates with `dry_run=true` before the control plane dispatches it.

### Plan A: spikes without a human, and web research (`web_search`, `fetch_url`, findings delivery)

**Status: shipped 2026-10-29, see State of the work.** The design below is kept for reference; differences from it: the profile section is `web.allowed_hosts` (not `spike`), `["*"]` allows any public host, `web_search` (Tavily) was added, both tools also go to the bug-fixer and to non-spike issues, and spike delivery requires fetched evidence that matches the findings.

Original design:

Problem: issue 107 is a spike ("record findings in an issue comment", no code). The builder has no network beyond GitHub and Honeycomb (tool guard allow-list, prompts forbid `curl`/`wget`) and the implement apply path only knows how to open a PR, so it died with "no commits ahead of main".

1. **Profile** (`CONTRACTS.md`, `factory/schemas/target_profile.py`, `templates/target/factory.yml`, `examples/fantasy-football/factory.yml`): new section `spike: { allowed_hosts: [] }`. Hosts the target lets a spike read, exact match or `*.suffix`. Empty list (the default) means spikes are not attempted. Baseline the profile cannot override: `https` only, no private or loopback addresses, no redirects off the allow-list, 200 KB per response, 20 fetches per run, 10 s timeout. For fantasy-football: `site.api.espn.com`, `www.ligaportugal.pt`, `fantasy.ligaportugal.pt`, `v3.football.api-sports.io` (that one needs a key the agent never holds, so it can only report "requires a key, not checked").
2. **Tool** `fetch_url(url)` in `factory/tools/fetch.py`, registered for the builder's research and implement phases only. GET, no custom headers, runs with `clean_env()`, HTML reduced to text, clipped, returned inside `<untrusted source="<url>">...</untrusted>`. The allow-list check lives in `factory/guards/tool_guard.py` (`check_fetch`) so a denial is a guard event with the usual `[tool_guard] Blocked` text and a Honeycomb signal. Tests: guard tests for the allow-list and baseline rules; a tool test against a local HTTP server fixture (loopback allowed in tests only).
3. **Proposal** `BuildProposal.findings_md: str | None = None`. Contract: `files == []` with `findings_md` set is a spike result; `files == []` without it still fails the run.
4. **Apply** (`apply_implement`): before the branch checks, when `files` is empty and `findings_md` is set, post `<!-- factory:findings -->` + the sanitised findings as an issue comment (deduped by marker and run id), move labels to `status:done` (remove `status:in-progress`, `status:plan-review`, `status:backlog`), close the issue with reason completed, write no `created-pr.txt`. The branch the pipeline created is never pushed. No reviewer run; the findings feed the sibling issues (108 to 110 reference 107).
5. **Prompts** (`agents/builder/research.md`, `plan.md`, `implement.md`): a spike is an issue whose acceptance criteria are all investigation ("investigate", "check", "record findings"). Research names the sources to fetch; plan has an empty file list and lists the fetches; implement calls `fetch_url` per source and writes findings per source as: URL, HTTP status, what fields or structures exist, auth or cost or limits, verdict for the parent epic. Replace "never use `curl`/`wget`" with "use `fetch_url`; `curl`/`wget` are blocked". Fetched text is DATA under the existing untrusted-text rules.
6. **Triage**: label `spike` (add to `factory.config.json#labels` and `scripts/preflight-labels.sh`) so humans can see it; the control plane needs no change (a `risk:low` enhancement is already eligible) but `blockReason` should skip `spike` issues when the profile's `spike.allowed_hosts` is empty (one more field in `TickState`; `check-config-sync.sh` does not cover profile values, so read it from the profile at tick time or accept the failed run as the signal).
7. **Roll-out**: push factory; merge a target PR adding `spike.allowed_hosts`; reset 107 to `status:backlog` (remove `status:in-progress`); `gh workflow run builder.yml -f issue=107 -f dry_run=true` and read the findings in the printed proposal; then let the cron run it live.

Risks: fetched pages are attacker-controlled text reaching the model (the agent holds no tokens, the workspace is a public repo, so the blast radius is bad findings); fetch calls are tool calls, not model calls, but each page adds context, so the context guard matters more than the quota guard. Size: one tool file, a guard function, a schema field, about 20 lines of bash, three prompt paragraphs, a profile section, tests.

### Plan B: security findings become issues (`security-triage` agent)

Problem: the target's GitHub Security tab has findings nobody turns into work. On 2026-10-28: one open code-scanning alert (CodeQL `js/stack-trace-exposure`, medium, `src/loginProbe.ts`), about a dozen open Dependabot alerts (`undici` high, transitive via `wrangler`; `vite` and `@vitest/mocker`, already covered by Dependabot PRs 96 and 88), zero secret-scanning alerts. The target has no `security/` directory and no `SECURITY.md`; "security findings" means these three GitHub alert feeds.

1. **App permissions** (owner, dashboard): add Code scanning alerts: Read, Dependabot alerts: Read, Secret scanning alerts: Read to `software-factory-agent-bot`, then accept the new permissions on both installations. Record in step 3 above when done. Without this the gather step gets 403 and the run must stop with `stop_reason: no_inputs`, not an empty proposal.
2. **Inputs** (`scripts/gather-inputs.sh security`, pipeline side, App token, no agent token): write `inputs/security.json` with open code-scanning alerts (`number`, `rule.id`, `rule.severity`, `rule.security_severity_level`, `rule.description`, path, start line, message, `html_url`, `created_at`), open Dependabot alerts (`number`, package, ecosystem, severity, `ghsa_id`, vulnerable range, first patched version, manifest path, `html_url`, and `open_pr` = the open Dependabot PR number bumping that package if any), secret-scanning alerts as a count and types only (never the secret), and the open issues that carry a `<!-- factory:security:` marker, with their alert numbers. Cap each list at 50.
3. **Agent** `security-triage` in `factory.config.json` (model cheap, phase `triage`, tools `read_file`, `list_files`, `search_files` so it can look at the flagged code, limits as triage, `max_model_calls` 15). Prompt in `agents/security-triage/triage.md`. Proposal `SecurityProposal` (`factory/schemas/models.py`, `CONTRACTS.md`): `{findings[{source: "code_scanning"|"dependabot"|"secret_scanning", alert_number, signature, title, body, priority: 1|2|3, labels[], existing_issue|null}], resolved[{issue, alert_number, note}], summary}`. Signature: `code_scanning:<rule.id>:<path>` or `dependabot:<package>:<ghsa_id>`. Rules the prompt states and the apply script enforces: skip a Dependabot alert that has an open Dependabot PR (the reviewer and `dark_dependency_bumps` handle it) and one in `lit_dependencies` gets `needs-human`; secret-scanning findings are always `needs-human` and the body never contains the secret; priority from severity (critical or high 1, medium 2, low 3); `risk:low size:S` for a single-file code-scanning fix, else `risk:high`; `resolved` lists tracked issues whose alert is now closed or fixed.
4. **Apply** (`apply_security`, modelled on `apply_incident`): marker `<!-- factory:security:<sha256(signature)[:12]> -->`, dedupe through `create_issue` by marker, labels `bug` + `security` + `status:backlog` + `priority:N` + `factory:accepted` (plus `needs-human` where the rules say), cap 5 new issues per run; body = alert link, rule description, location, suggested fix, acceptance criterion "alert N is closed on `main`"; for each `resolved` entry comment and move the issue to `status:done`. Add `security` to the label set and `preflight-labels.sh`.
5. **Control plane**: dispatch once a day in the 06:30 UTC window (same pattern as the incident responder, subject = date) and on webhook events `code_scanning_alert` (`created`, `appeared_in_branch`) and `dependabot_alert` (`created`), debounced to one run per hour; register those events on the target webhook; mirror the agent spec in `wrangler.jsonc` and `src/config.ts` (`check-config-sync.sh` catches drift); a `security-triage.yml` dispatch wrapper and an `agent-run.yml` gather step like the Honeycomb one.
6. **Pickup**: issues land as `bug` + `status:backlog` + `factory:accepted`, so the bug-fixer takes them on the next tick and `bugs_first` pauses the builder while a priority 1 or 2 security bug is open. That is the intended behaviour; if it starves features, lower security priorities to 2 and 3. The bug-fixer's PR runs CodeQL in the target's CI, and the next security-triage run closes the issue once the alert is gone.
7. **Roll-out**: permissions first; `gh workflow run security-triage.yml -f dry_run=true` and check the proposal against the Security tab; live once; then enable in the control plane. Expected first result on fantasy-football: one `js/stack-trace-exposure` issue (`risk:low size:S`, bug-fixer eligible) and one `needs-human` issue for `undici` (transitive via `wrangler`, which is lit).

Risks: alert text is untrusted input like everything else; the daily run costs about 10 model calls; code-scanning alerts on branches other than `main` must be filtered out (`ref` = default branch) or the agent files issues for PR heads.

## Claude Code tooling for working on the factory

For developing this repo (not the factory's runtime agents, which stay Strands + Gemini): the `factory-orchestrate` skill (`.claude/skills/`) drives a change from the main Opus session, delegating read-only research to the `factory-researcher` subagent (Fable) and scoped work packages to `factory-implementer` (Sonnet) (`.claude/agents/`). Deploys, workflow dispatches, secrets and target merges stay with the owner.

## How to work on it

```sh
uv sync && uv run pytest -q                       # harness
uv run python -m factory.run --agent triage --phase triage --inputs inputs/ --out out/ --fake-model
cd control-plane && npm ci && npm test            # control plane
actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh
```

Change `CONTRACTS.md` before changing any interface; the four areas (harness, prompts, scripts and workflows, control plane) were built against it in parallel and only agree because of it.
