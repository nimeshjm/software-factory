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
npx wrangler secret put ALERT_WEBHOOK_URL      # optional, https only (Slack/Discord)

npx wrangler deploy
```

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
