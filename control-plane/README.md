# factory-control-plane

Cloudflare Worker + D1 control plane for the software factory. Receives GitHub webhooks, runs a `*/15` cron pre-flight (`src/preflight.ts`, pure and unit-tested), dispatches `workflow_dispatch` runs in the factory repo under a per-(agent, subject) lock, and keeps the run ledger and daily model-call quota. HTTP contract: `../CONTRACTS.md`.

## Deploy

```bash
cd control-plane && npm install
npx wrangler d1 create factory          # copy database_id into wrangler.jsonc
npx wrangler d1 migrations apply factory --remote

# secrets (never vars)
npx wrangler secret put FACTORY_APP_ID
# GitHub gives a PKCS#1 key (BEGIN RSA PRIVATE KEY); WebCrypto needs PKCS#8:
openssl pkcs8 -topk8 -inform PEM -outform PEM -nocrypt -in app.pem -out app-pkcs8.pem
npx wrangler secret put FACTORY_APP_PRIVATE_KEY < app-pkcs8.pem
npx wrangler secret put GITHUB_WEBHOOK_SECRET
npx wrangler secret put FACTORY_TOKEN          # bearer for /runs/* and /admin/*
npx wrangler secret put HONEYCOMB_API_KEY      # optional (ingest key)
npx wrangler secret put ALERT_WEBHOOK_URL      # optional, https only (Slack/Discord): failed or timed-out runs, lost runs, tick and dispatch failures

npx wrangler deploy
```

After the first manual deploy, CI redeploys automatically: the `deploy-control-plane` job in `.github/workflows/ci.yml` runs on every push to `main` once the control-plane tests and lint jobs pass, applies D1 migrations with `--remote`, then runs `wrangler deploy`. It needs the repo secrets `CLOUDFLARE_API_TOKEN` (API token with Workers Scripts: Edit and D1: Edit on the account) and `CLOUDFLARE_ACCOUNT_ID`; without them the job logs a warning and skips. Secrets set with `wrangler secret put` are not touched by a deploy.

The GitHub App must be installed on both `<owner>/<target>` (read issues/PRs) and `<owner>/<factory>` (Actions: write).

Register the webhook on the target repo (or configure it on the App):

```bash
gh api repos/<owner>/<target>/hooks -X POST \
  -f name=web -F active=true \
  -f 'config[url]=https://factory-control-plane.<subdomain>.workers.dev/webhooks/github' \
  -f 'config[content_type]=json' -f "config[secret]=$GITHUB_WEBHOOK_SECRET" \
  -f 'events[]=issues' -f 'events[]=pull_request' -f 'events[]=check_suite' -f 'events[]=workflow_run'
```

Vars in `wrangler.jsonc` (non-secret):

| Var | Meaning |
|---|---|
| `TARGET_REPO` / `FACTORY_REPO` | `<owner>/<target>` and `<owner>/<factory>`; mirror `factory.config.json` |
| `TARGET_DEFAULT_BRANCH` | target branch whose successful deploy triggers the verifier; mirrors `deploy.branch` in the target's `factory.yml` |
| `DEPLOY_WORKFLOW_NAME` | name of the target's deploy workflow; mirrors `deploy.workflow_name` in `factory.yml` |
| `FACTORY_REF` | ref of the factory repo that `workflow_dispatch` runs against (default `main`) |
| `DAILY_MODEL_CALLS` / `MAX_OPEN_FACTORY_PRS` | quota and PR cap; mirror `factory.config.json` |
| `HONEYCOMB_DATASET` | Honeycomb dataset for events |

`scripts/check-config-sync.sh` verifies these match `factory.config.json` and the target's `factory.yml`.

Operate: `curl -H "Authorization: Bearer $FACTORY_TOKEN" -X POST <url>/admin/pause` (or `/admin/resume`); `curl <url>/status` (a browser gets an HTML table).

## Status endpoint

`GET /status` is the control plane's health page: one unauthenticated read that answers "is the factory running, what is it doing, and why did the last tick do nothing". It needs no token (it exposes only run ids, agent names, issue and PR numbers and quota counts), returns JSON by default, and renders the same data as an HTML table when the request's `Accept` header includes `text/html`, so opening `https://factory-control-plane.nimeshjm.workers.dev/status` in a browser works.

```bash
curl -s $FACTORY_URL/status | jq
```

```json
{
  "paused": false,
  "today": { "model_calls": 156, "reserved": 0, "quota": 400 },
  "in_flight": [
    { "run_id": "builder-202610282230-issue-104", "agent": "builder", "subject": "issue:104",
      "status": "started", "dispatched_at": "...", "started_at": "...", "workflow_run_url": "..." }
  ],
  "last_tick": {
    "at": "2026-10-28T23:00:36.100Z",
    "decisions": [
      { "kind": "skip", "agent": "builder", "reason": "no_eligible_feature (#35:awaiting-risk-or-plan-approval, #100:author-not-trusted)" }
    ]
  }
}
```

| Field | Meaning |
|---|---|
| `paused` | the kill switch (`POST /admin/pause`); while true the cron tick dispatches nothing |
| `today.model_calls` | model calls reported by runs finished since 00:00 UTC |
| `today.reserved` | calls reserved by runs still in flight (each agent's `max_model_calls`) |
| `today.quota` | `DAILY_MODEL_CALLS`; a dispatch is skipped with `quota` when used plus reserved plus the agent's cap would exceed it |
| `in_flight` | runs in `dispatched` or `started` state, with the Actions run URL once the harness has reported `started` |
| `last_tick` | when the cron last ran (every 15 minutes) and every decision it took; `null` until the first tick |

Decision kinds in `last_tick.decisions`:

- `dispatch`: an agent was started; `subject` is `issue:N` for triage, builder and bug-fixer, `dependabot` for the hourly review (the PR number travels in the inputs), or the date for the incident responder.
- `mark_lost`: a run exceeded its timeout plus grace without reporting `finished`; its reservation is released and the ledger marks it `lost`.
- `skip`: the agent was considered and not started. `reason` is one of `in_flight` (that agent already has a run), `no_spec`, `quota`, `backpressure` (open factory PRs at `MAX_OPEN_FACTORY_PRS`), `bugs_first` (a priority 1 or 2 bug is open, so the builder waits), or `no_eligible_feature (...)` / `no_eligible_bug (...)` listing every backlog candidate with why it was passed over: `has-open-pr`, `needs-human`, `author-not-trusted` (author is not a collaborator and the issue lacks `factory:accepted`), `blocked-by:#N` (an open `Depends on` issue), or `awaiting-risk-or-plan-approval` (not `risk:low` and no `plan:approved`). The list is capped at 10 entries with `+N more`.

An agent absent from the decisions was not a candidate on that tick (nothing in its backlog, or it runs on a schedule such as the hourly Dependabot review and the daily incident responder).

## Local dev

```bash
touch .dev.vars   # secrets as KEY=value lines (PEM with \n escapes)
npx wrangler d1 migrations apply factory --local
npx wrangler dev --test-scheduled
curl "http://localhost:8787/__scheduled?cron=*/15+*+*+*+*"   # run a tick
# replay a webhook
BODY=$(cat payload.json); SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$GITHUB_WEBHOOK_SECRET" | sed 's/.* //')
curl -X POST localhost:8787/webhooks/github -H "x-github-event: issues" -H "x-github-delivery: $(uuidgen)" \
  -H "x-hub-signature-256: sha256=$SIG" --data "$BODY"
```

Tests: `npm test` (plain node vitest; WebCrypto is global). `npm run typecheck` for tsc.

## Free-tier limits that matter

- Cron triggers: 5 per account on free; this Worker uses 1.
- D1: 100k rows written/day, 5M read/day. A tick writes a handful of rows; the ledger writes about 3 per run.
- Subrequests: 50 per invocation. A tick does at most: 2 GitHub list calls (up to 3 pages each via the `maxPages` cap), 0-4 token/installation calls (cached in `tokens` until 5 min before expiry), 1 dispatch per dispatch decision, 1 Honeycomb event per decision, alerts. Do not raise `maxPages` without budgeting.
- CPU: 10 ms per request on free; JWT signing only happens on a token cache miss (about hourly).

## Notes

- `src/config.ts` mirrors per-agent limits from `factory.config.json`; keep them in sync. Builder and bug-fixer are dispatched with empty `phase` (all phases) and reserve/timeout the sum of their phases.
- Factory-filed issues must carry `factory:accepted` (or come from OWNER/MEMBER/COLLABORATOR) to pass the author gate.
