# Handoff: software factory

Written 2026-10-28, rewritten 2026-10-29 22:45 UTC. Everything below is the state at that time.

## Do next (2026-10-30, after 07:00 UTC)

The Gemini flash quotas reset at midnight Pacific (07:00 UTC). Issue 107's live run failed on tokens on 2026-10-29 and the fix (`cb52a4f`) is untested. In order:

1. **Dry-run the spike on 107:** `gh workflow run builder.yml -f issue=107 -f dry_run=true`. 107 is at `status:in-progress`; a manual dispatch ignores labels.
2. **Check the run.**
   - Step log: `[tool]` lines, and any `[model] ... switching to ...` lines.
   - Per phase in the artifact (`gh run download <id>`): `phases/<phase>/result.json` for `model_ids_used`, `model_calls` and `usage.total_tokens`. Expected: research on `gemini-3.8-flash` with at most 25 calls; implement on `gemini-3.5-flash-lite` with at most 15 calls and under 300k tokens, fetching little or nothing.
   - Claim check: `uv run python -m factory.policy check-findings --proposal <dir>/phases/implement/proposal.json --results <dir>/phases/research/result.json <dir>/phases/implement/result.json` must print `"ok": true`.
   - Read `findings_md` by hand:
     - It names `elements[].stats.in_dreamteam` (fantasy `event/<n>/live/`) and `events[].top_element_info` (`bootstrap-static/`).
     - ESPN is checked on the `summary` of a finished `por.1` match.
     - API-Football is reported as 403 without a key.
     - No unsourced claims.
   - Honeycomb: environment and dataset `software-factory`, filter `factory.run_id = gh-<run id>-1`, break down by `factory.phase` and `gen_ai.request.model`.
3. **If good:** `gh issue edit 107 -R nimeshjm/fantasy-football --remove-label status:in-progress --add-label status:backlog`. The cron runs it live within 15 minutes. Success: a `<!-- factory:findings -->` comment ending in the pipeline's `## Evidence` list, `status:done`, and the issue closed. This is the first live use of the spike apply branch against GitHub.
4. **Then release 117:** `gh issue edit 117 -R nimeshjm/fantasy-football --remove-label needs-human`. Only one spike a day fits the flash budget comfortably, so do this after 107 is done, or the next day.
5. **If the dry run is bad:** compare against the history below. The levers are the spike prompts (`agents/builder/{research,plan,implement}.md`, the spike bullets), `models.spike_research` / `models.spike` in `factory.config.json`, and the `research` token limit.
6. **Issue 116 (remove API-Football)** is `risk:high`, so the builder waits for `plan:approved`, and its PR needs your review. Also remove the `API_FOOTBALL_KEY` line from fantasy-football's `factory.yml` yourself; agents cannot edit that file.

## What this is

An autonomous software factory: background agents that triage issues, plan and build features, fix bugs, review and merge PRs, verify deploys, and turn production errors into bug issues. It targets one repository per deployment, configured in `factory.config.json` (currently `nimeshjm/fantasy-football`). The design follows the Horthy/Osmani factory model and the constraints in the author's blog series (deterministic guards, plan before code, small diffs, agent-plus-human review split, lights-off autonomy earned per loop).

Stack, decided by the owner: Strands Agents (Python) on the Gemini API free tier; GitHub Actions as orchestration and sandbox; a free-tier Cloudflare Worker as control plane; Honeycomb via OpenTelemetry. Not Claude Code.

## Where things are

| Location | What |
|---|---|
| `/Users/admin/repos/software-factory` | this repo, the factory, public at `nimeshjm/software-factory` |
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

**Open issues on fantasy-football:**
- **107** spike, `status:in-progress`, nothing posted. See Do next.
- **117** "Spike other football API providers", `spike risk:low`, held with `needs-human`.
- **116** "Remove API-Football", `risk:high`, waits for `plan:approved`.
- **108 to 110** awards implementation (epic 106) and **100 to 103** (epic 35) are blocked on the author gate or `risk:high`.

**Verification at `cb52a4f` (2026-10-29):**

| Check | Result |
|---|---|
| `uv run pytest -q` | 186 passed |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 27 passed, clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| CI on `main` | green on all jobs |

**Setup is complete:**
- **GitHub App** `software-factory-agent-bot` (id 5514689), installed on both repos.
- **Repo secrets:** `GEMINI_API_KEY` (dedicated Google Cloud project `gen-lang-client-0040576374`), `FACTORY_APP_ID`, `FACTORY_APP_PRIVATE_KEY`, `HONEYCOMB_API_KEY`, `HONEYCOMB_QUERY_KEY` (unused), `SMOKE_AUTH_TOKEN`, `FACTORY_TOKEN`, `TAVILY_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- **Repo variables:** `FACTORY_URL`, `TARGET_BASE_URL`.
- **Worker:** secret `ALERT_WEBHOOK_URL` points at Discord; the target webhook is registered for `issues, pull_request, check_suite, workflow_run`.
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
- **Flash budget:** 60 flash requests a day across three models, so about two spikes a day. When every model on a list is exhausted the run fails and its issue stays `status:in-progress`. A switch can finish a run on an older model. The control plane doesn't track exhaustion, so each run spends one request per exhausted model to find out.
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
- **Stuck runs:** any failed builder or bug-fixer implement run leaves its issue `status:in-progress`, and a human resets it to `status:backlog`.

**Merge and review**
- **Dependabot:** at most one merge per reviewer run, cron-triggered only. `wrangler` and `@cloudflare/*` bumps are always lit (`lit_dependencies`). The merge-policy trigger is inferred from the PR author.
- **Review debounce:** reviews are debounced per PR for 5 minutes, so a push during an in-flight review may leave that review covering the older commit.

**Platform**
- **Honeycomb reads:** stays `enabled: false` in the target profile. The Query Data API is Enterprise-only, so liveness and error polling can't work on the free plan; the verifier falls back to smoke checks and the incident responder emits nothing. Ingest works (EU region). To restore reads, port `scripts/honeycomb-query.sh` and `factory/tools/honeycomb.py` to the Honeycomb MCP server (all plans).
- **Config mirroring:** the control plane mirrors agent limits in `wrangler.jsonc` and `src/config.ts`, and `scripts/check-config-sync.sh` catches drift. Its tick must stay under the free plan's 50 subrequests.
- **Example profile secret rule:** it treats `FANTASY_(EMAIL|PASSWORD|SESSION_COOKIE)=value` as a leak; bare variable names are allowed.
- **Multi-target** is out of scope; the config shape allows it later.

## Planned work

Plan A (spikes and web research) shipped on 2026-10-29; `CONTRACTS.md` is the reference. Plan B below is not started. It follows the core rules: agents propose and `apply-proposal.sh` acts; new interfaces go into `CONTRACTS.md` first; the agent graduates with `dry_run=true` before the control plane dispatches it.

### Plan B: security findings become issues (`security-triage` agent)

Problem: the target's GitHub Security tab has findings nobody turns into work. On 2026-10-28 it had:
- one open code-scanning alert (CodeQL `js/stack-trace-exposure`, medium, `src/loginProbe.ts`);
- about a dozen open Dependabot alerts (`undici` high, transitive via `wrangler`; `vite` and `@vitest/mocker`, already covered by Dependabot PRs 96 and 88);
- zero secret-scanning alerts.

"Security findings" means these three GitHub alert feeds.

1. **App permissions** (owner, in the dashboard): add Code scanning alerts: Read, Dependabot alerts: Read and Secret scanning alerts: Read to `software-factory-agent-bot`, then accept the new permissions on both installations. Without this the gather step gets 403, and the run must stop with `stop_reason: no_inputs`, not an empty proposal.
2. **Inputs** (`scripts/gather-inputs.sh security`, pipeline side, App token, no agent token): write `inputs/security.json` with:
   - open code-scanning alerts: `number`, `rule.id`, `rule.severity`, `rule.security_severity_level`, `rule.description`, path, start line, message, `html_url`, `created_at`;
   - open Dependabot alerts: `number`, package, ecosystem, severity, `ghsa_id`, vulnerable range, first patched version, manifest path, `html_url`, and `open_pr` (the open Dependabot PR bumping that package, if any);
   - secret-scanning alerts as a count and types only, never the secret;
   - open issues carrying a `<!-- factory:security:` marker, with their alert numbers.

   Cap each list at 50.
3. **Agent** `security-triage` in `factory.config.json`: model cheap, phase `triage`, tools `read_file`, `list_files` and `search_files`, limits as triage, `max_model_calls` 15. Prompt in `agents/security-triage/triage.md`.
   - Proposal `SecurityProposal` (`factory/schemas/models.py`, `CONTRACTS.md`): `{findings[{source: "code_scanning"|"dependabot"|"secret_scanning", alert_number, signature, title, body, priority: 1|2|3, labels[], existing_issue|null}], resolved[{issue, alert_number, note}], summary}`.
   - Signature: `code_scanning:<rule.id>:<path>` or `dependabot:<package>:<ghsa_id>`.
   - Rules the prompt states and the apply script enforces:
     - skip a Dependabot alert that has an open Dependabot PR;
     - an alert on a package in `lit_dependencies` gets `needs-human`;
     - secret-scanning findings are always `needs-human`, and the body never contains the secret;
     - priority from severity: critical or high is 1, medium 2, low 3;
     - `risk:low size:S` for a single-file code-scanning fix, else `risk:high`;
     - `resolved` lists tracked issues whose alert is now closed.
4. **Apply** (`apply_security`, modelled on `apply_incident`):
   - Marker `<!-- factory:security:<sha256(signature)[:12]> -->`, deduplicated through `create_issue`.
   - Labels `bug`, `security`, `status:backlog`, `priority:N` and `factory:accepted`, plus `needs-human` where the rules say.
   - At most 5 new issues per run.
   - Body: alert link, rule description, location, suggested fix, and the acceptance criterion "alert N is closed on `main`".
   - For each `resolved` entry: comment, then move the issue to `status:done`.
   - Add `security` to the label set and `preflight-labels.sh`.
5. **Control plane:**
   - Dispatch once a day in the 06:30 UTC window, and on webhook events `code_scanning_alert` (`created`, `appeared_in_branch`) and `dependabot_alert` (`created`), debounced to one run an hour.
   - Register those events on the target webhook.
   - Mirror the agent spec in `wrangler.jsonc` and `src/config.ts`.
   - Add a `security-triage.yml` dispatch wrapper and an `agent-run.yml` gather step.
6. **Pickup:** issues land as `bug status:backlog factory:accepted`, so the bug-fixer takes them. `bugs_first` pauses the builder while a priority 1 or 2 security bug is open.
7. **Roll-out:**
   1. Permissions.
   2. `gh workflow run security-triage.yml -f dry_run=true`, and check the proposal against the Security tab.
   3. Live once, then enable in the control plane.

   Expected first result: one `js/stack-trace-exposure` issue (`risk:low size:S`) and one `needs-human` issue for `undici`.

Risks: alert text is untrusted input; the daily run costs about 10 model calls; code-scanning alerts on branches other than `main` must be filtered out (`ref` = default branch).

## Claude Code tooling for working on the factory

For developing this repo (not the factory's runtime agents, which stay Strands + Gemini), the `factory-orchestrate` skill (`.claude/skills/`) drives a change from the main Opus session. It delegates read-only research to the `factory-researcher` subagent (Fable) and scoped work packages to `factory-implementer` (Sonnet) (`.claude/agents/`). Review the implementer's diff yourself: its tests have missed real-API behaviour (the `messageStart` ordering), and one report came back empty. Deploys, workflow dispatches, secrets and target merges stay with the owner.

## How to work on it

```sh
uv sync && uv run pytest -q                       # harness
uv run python -m factory.run --agent triage --phase triage --inputs inputs/ --out out/ --fake-model
cd control-plane && npm ci && npm test            # control plane
actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh
```

Change `CONTRACTS.md` before changing any interface; the four areas (harness, prompts, scripts and workflows, control plane) were built against it in parallel and only agree because of it.
