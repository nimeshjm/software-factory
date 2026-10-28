# Handoff: software factory

Written 2026-10-28. Everything below is the state at that date.

## What this is

An autonomous software factory: background agents that triage issues, plan and build features, fix bugs, review and merge PRs, verify deploys, and turn production errors into bug issues. It targets one repository per deployment, configured in `factory.config.json` (currently `nimeshjm/fantasy-football`). The design follows the Horthy/Osmani factory model and the constraints in the author's blog series (deterministic guards, plan before code, small diffs, agent-plus-human review split, lights-off autonomy earned per loop).

Stack, decided by the owner: Strands Agents (Python) on the Gemini API free tier; GitHub Actions as orchestration and sandbox; a free-tier Cloudflare Worker as control plane; Honeycomb via OpenTelemetry. Not Claude Code.

## Where things are

| Location | What |
|---|---|
| `/Users/admin/repos/software-factory` | this repo, the factory |
| `/Users/admin/repos/fantasy-football` | the target, local clone, branch `chore/factory-onboarding` checked out |
| `~/.claude/plans/i-want-to-build-moonlit-beacon.md` | plan for the initial build |
| `~/.claude/plans/make-the-fantasy-application-keen-puddle.md` | plan for the target-agnostic refactor |
| `CONTRACTS.md` | the shared shapes every part must honour; change it first |
| `README.md`, `scripts/README.md`, `control-plane/README.md`, `agents/README.md` | runbooks per area |

## Repo layout

- `factory/` Python harness: `run.py` entrypoint, `guards/` (tool, quota, latency, context, output), `tools/` (workspace-scoped file, shell, checks, git, Honeycomb), `policy/` (autonomy, merge, author gate), `schemas/` (proposal models and the `TargetProfile`), `prompts.py` (renders `{{target_profile}}`).
- `agents/` system prompts per agent and phase, plus the split review template.
- `scripts/` the only code that mutates GitHub (`apply-proposal.sh`), input gathering, deploy polling, Honeycomb queries, label bootstrap, config-sync check.
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

Step 8 progress: triage is graduated (dry run, then live on issue 35: labels applied, child issues 100 to 103 created). The reviewer passed its dry run on Dependabot PR 96 (verdict approve, 5 model calls); a live run would post the review and label it lit without merging. Not yet run: verifier, builder, bug-fixer. Incident responder is deferred (see Honeycomb below).

**The control plane is paused** (`POST /admin/pause`) because its first cron tick dispatched live triage and reviewer runs before any loop was graduated. It has no per-agent gate. Resume with `POST /admin/resume` once the owner is happy with live triage and reviewer; from then on it dispatches triage for unlabeled issues and the Dependabot reviewer hourly.

Fixes made while graduating, all pushed: issue and PR inputs come from the REST API (`gh --json` has no `authorAssociation`); Google retired the 2.5 Gemini models for new keys, so both tiers use `gemini-3.5-flash(-lite)` with `thinking_level: minimal`; Honeycomb endpoints point at the EU region; the cron passes the oldest Dependabot PR number to the reviewer (a run without `pr` reviews nothing and still spends ~18 model calls). The control plane needs a `wrangler deploy` after the last of these.

Verification at handoff:

| Check | Result |
|---|---|
| `uv run pytest -q` | 95 passed |
| `cd control-plane && npm test && npx tsc --noEmit && npx wrangler deploy --dry-run` | 22 passed, clean, succeeds |
| `actionlint`, `shellcheck -x scripts/*.sh`, `scripts/check-config-sync.sh` | clean |
| profile validation on example and template | both valid; empty file rejected |
| deny-list composition | example profile blocks `wrangler` and `EVAL_LIVE`, template only baseline; `git push` and `factory.yml` edits blocked in both |

Not exercised against live services: the real Gemini path (only the fake model ran end to end), Honeycomb queries and OTLP export, `gh` mutations in `apply-proposal.sh`, the Playwright smoke run, the control plane against real GitHub webhooks. All of these have unit tests with mocks or lint only.

**Target repo: onboarding PR open.** `nimeshjm/fantasy-football` PR 99 (`chore/factory-onboarding`) adds `factory.yml`, `.agents/*`, `docs/product-spec.md`, a token-gated `GET /version`, issue and PR templates, and doc fixes. CI, CodeQL and analysis checks are green. It is not merged.

## Setup still owed by the owner (in order)

1. Done: pushed, public.
2. Done (PR 99 merged, `GET /version` returns the live SHA). Original note: Merge PR 99 in fantasy-football. After its first deploy, confirm `GET /version` (header `x-dashboard-token`) returns the commit SHA, not `unknown`; the `--var` pass-through in `cloudflare/wrangler-action` is unverified.
3. Done. Create GitHub App `software-factory-agent-bot` (the name `factory-bot` was taken; `factory.app_slug` is updated to match) (Contents RW, Issues RW, Pull requests RW, Actions RW, Checks R, Metadata R, no Workflows). Install on both repos. Convert the key with `openssl pkcs8 -topk8 -nocrypt` for the Worker.
4. Done. Secrets in this repo: `GEMINI_API_KEY` (dedicated Google Cloud project; Google AI Plus does not cover the API), `FACTORY_APP_ID`, `FACTORY_APP_PRIVATE_KEY`, `HONEYCOMB_API_KEY`, `HONEYCOMB_QUERY_KEY`, `SMOKE_AUTH_TOKEN` (the dashboard token), `FACTORY_TOKEN`. Variables: `FACTORY_URL`, `TARGET_BASE_URL`.
5. Done. Labels: run `scripts/preflight-labels.sh` against the target. Ruleset on `main` per `examples/fantasy-football/ONBOARDING.md`.
6. Done. Deploy the control plane (`control-plane/README.md`), set its secrets, register the webhook for `issues, pull_request, check_suite, workflow_run`.
7. Honeycomb: stays `enabled: false` in the target's `factory.yml`. The read path (`scripts/honeycomb-query.sh`, `factory/tools/honeycomb.py`) uses the Query Data API, which Honeycomb gates to Enterprise; on the free plan a configuration key cannot carry Run Queries, so liveness and error polling cannot work. The verifier falls back to the smoke checks and the incident responder emits an empty list by design. Ingest (factory telemetry via OTLP and control-plane events) works on the free plan and is wired to the EU region. To get the read path back, port it to the Honeycomb MCP server (`honeycomb.mcp_url`, available on all plans) and then revisit the `faas.trigger` liveness filter.
8. Graduate loops in order, each first with `dry_run=true` via `gh workflow run <agent>.yml -f dry_run=true ...`: triage, reviewer on Dependabot PRs, verifier, builder on a `risk:low size:S` issue, bug-fixer, incident responder.

## Known gaps and judgement calls

- Issue `#35` in the target is epic-sized; triage is expected to slice it into child issues on its first run.
- Dependabot: at most one merge per reviewer run, cron-triggered only; `wrangler` and `@cloudflare/*` bumps are always lit. The merge-policy trigger is inferred from the PR author, not from a real cron signal.
- Reviewer runs are debounced per PR for 5 minutes; a push during an in-flight review is skipped, so that review may cover the older commit.
- Dependency-only PR detection (`toolchain.dependency_files`) is wired but does not change merge behaviour.
- The control plane mirrors agent limits and identity in `wrangler.jsonc` and `src/config.ts`; `scripts/check-config-sync.sh` in CI catches drift. Its tick must stay under the free plan's 50 subrequests.
- `--max-budget`-style spend control does not exist for Gemini; the quota guard counts model calls, including throttle retries, so a 429 storm can exhaust a run's cap early.
- Summarisation inside Strands' context manager makes model calls the quota guard does not see, so ledger call counts can under-report slightly.
- The example profile's secret rule treats `FANTASY_(EMAIL|PASSWORD|SESSION_COOKIE)=value` as a leak; bare variable names are allowed.
- Multi-target is out of scope; the config shape allows it later.
- `HONEYCOMB_QUERY_KEY` is set as a repo secret but the key cannot run queries (free plan); it is unused while `honeycomb.enabled` is false.

## How to work on it

```sh
uv sync && uv run pytest -q                       # harness
uv run python -m factory.run --agent triage --phase triage --inputs inputs/ --out out/ --fake-model
cd control-plane && npm ci && npm test            # control plane
actionlint && shellcheck -x scripts/*.sh && scripts/check-config-sync.sh
```

Change `CONTRACTS.md` before changing any interface; the four areas (harness, prompts, scripts and workflows, control plane) were built against it in parallel and only agree because of it.
