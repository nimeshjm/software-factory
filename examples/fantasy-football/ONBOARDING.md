# Onboarding overlay for nimeshjm/fantasy-football (worked example)

This is the worked example of the generic template in `templates/target/`. The full target profile lives in `factory.yml`.

This directory mirrors the target repo's paths. Applying it makes one PR.

## Steps

1. Clone the target and branch from current `main` (the three code files were copied from `origin/main` at authoring time, after PRs #93/#94 added `src/telemetry.ts`; re-diff if `main` moved):
   ```bash
   git clone git@github.com:nimeshjm/fantasy-football.git && cd fantasy-football
   git switch -c chore/factory-onboarding
   ```
2. Copy the overlay over it (excluding this file):
   ```bash
   rsync -a --exclude ONBOARDING.md /path/to/software-factory/examples/fantasy-football/ ./
   ```
3. Review the three code files as a diff and confirm only the `/version` change appears:
   ```bash
   git diff -- src/index.ts src/env.ts .github/workflows/deploy.yml CONTRIBUTING.md AGENTS.md
   ```
   Expected: `isAuthorized` import and a `GET /version` branch in `index.ts`; optional `GIT_SHA?`/`DEPLOYED_AT?` in `Env`; a "Stamp deploy time" step and `command: deploy --var GIT_SHA:... --var DEPLOYED_AT:...` in `deploy.yml`; the CI sentence in `CONTRIBUTING.md`; the appended "Working in this repo" section in `AGENTS.md`.
4. Verify: `npm ci && npm run typecheck && npm run lint && npm test`.
5. Commit (`Add factory onboarding overlay and GET /version`), push the branch, open the PR from `.github/pull_request_template.md`. After merge, `deploy.yml` runs; check `curl -H "x-dashboard-token: $DASHBOARD_TOKEN" https://<worker>/version` returns `{sha, deployedAt}` and that a request without the token returns 404.
6. Merge this new `factory.yml` into the target's default branch BEFORE upgrading the factory to the target-agnostic version. The factory fails a run when the profile is missing or invalid; a legacy `smoke.version_path` key is still accepted (with a warning) and migrated to `smoke.version`, so an older `factory.yml` keeps working meanwhile.
7. In the software-factory repo, set the secret `SMOKE_AUTH_TOKEN` to the same value as the target's dashboard token (it replaces the former `DASHBOARD_TOKEN` secret in the factory repo; the target's own `DASHBOARD_TOKEN` is unchanged).
8. Apply the repo settings below, then run the factory's labels script (labels are created by `scripts/preflight-labels.sh` in the software-factory repo, not by this overlay).

Note: `deploy.yml` passes vars with `--var` on the `wrangler deploy` command. The wrangler-action README documents `command:` but not a `vars` input, so `command:` is used. If the first deploy shows `sha: "unknown"`, check that the flag reached wrangler in the action log.

## Repo settings (`gh api`)

CI is `.github/workflows/ci.yml`, workflow `CI`, job `build-and-test`; the check context is `build-and-test`. It triggers on `push` only, which still reports on a PR's head commit.

```bash
REPO=nimeshjm/fantasy-football

# Merged branches are deleted; squash-only keeps history linear.
gh api -X PATCH repos/$REPO \
  -f delete_branch_on_merge=true -F allow_squash_merge=true \
  -F allow_merge_commit=false -F allow_rebase_merge=false

# Find the factory-bot App id (the App must be installed on the repo first).
APP_ID=$(gh api repos/$REPO/installation --jq .app_id 2>/dev/null || gh api /apps/factory-bot --jq .id)

# Ruleset on the default branch.
gh api -X POST repos/$REPO/rulesets --input - <<JSON
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": $APP_ID, "actor_type": "Integration", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "build-and-test" } ] } },
    { "type": "required_linear_history" },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
```

Notes:
- `required_approving_review_count` is 0 so the owner can merge their own and Dependabot PRs; lit factory PRs wait for a human `APPROVED` review enforced by the factory's `merge_policy`, not by GitHub. Raise to 1 if you want GitHub to enforce it too (the owner then cannot approve their own PRs; the bypass actor covers the factory only).
- The `factory-bot` bypass lets the App squash-merge dark PRs without a review. The App needs no Workflows permission; PRs touching `.github/**` are never dark.
- If `repos/$REPO/installation` is not accessible with your token, look up the App id in GitHub Settings > Developer settings > GitHub Apps > factory-bot and set `APP_ID` by hand.
- The Dependabot PRs currently open keep working: they get the `build-and-test` check from their branch pushes.
