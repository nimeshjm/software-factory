# Onboarding a target repo

Applying this template makes one PR in the target repo. Replace `<owner>/<repo>` with the target.

## Steps

1. Clone the target and branch from its default branch:
   ```bash
   git clone git@github.com:<owner>/<repo>.git && cd <repo>
   git switch -c chore/factory-onboarding
   ```
2. Copy the template over it, excluding this file:
   ```bash
   rsync -a --exclude ONBOARDING.md /path/to/software-factory/templates/target/ ./
   ```
   Do not overwrite existing files blindly: merge the issue and PR templates and any existing `AGENTS.md`.
3. Fill in `factory.yml` (every value in the template is a default; see its comments). At minimum set `toolchain` (runtime, setup, checks), `protected`, `secrets`, `risk`, and `dark_paths` (start empty). Validate it from the software-factory repo:
   ```bash
   uv run python -m factory.schemas validate-profile --file /path/to/<repo>/factory.yml
   ```
4. Fill `.agents/architecture.md`, `.agents/conventions.md`, `.agents/design.md` and `docs/product-spec.md`. All domain knowledge lives there; the factory prompts contain none.
5. Version endpoint: add one that returns the deployed commit SHA (and expose it only behind an auth header if it is sensitive), then set `smoke.version` in `factory.yml`. If you have none, set `smoke.version: null`; the deploy-identity check is then skipped.
6. Verify the checks locally, commit, push the branch and open the PR from `.github/pull_request_template.md`. Merge it into the default branch BEFORE running the factory: it reads `factory.yml` from there and fails a run when the profile is missing or invalid.
7. In the software-factory repo, set the secret `SMOKE_AUTH_TOKEN` to the token the version endpoint expects (leave it unset if the endpoint is public or `smoke.version` is null).
8. Create the labels with the factory's script, run from the software-factory repo: `scripts/preflight-labels.sh` (labels are created there, not by this template).
9. Install the GitHub App `factory-bot` (the `factory.app_slug` in `factory.config.json`) on the target repo, then apply the repo settings below.

## Repo settings (`gh api`)

Replace `required_status_checks` contexts with the check names your CI reports (the job names of the workflows that must pass).

```bash
REPO=<owner>/<repo>

# Merged branches are deleted; squash-only keeps history linear.
gh api -X PATCH repos/$REPO \
  -f delete_branch_on_merge=true -F allow_squash_merge=true \
  -F allow_merge_commit=false -F allow_rebase_merge=false

# Find the App id (the App must be installed on the repo first).
APP_ID=$(gh api repos/$REPO/installation --jq .app_id 2>/dev/null || gh api /apps/factory-bot --jq .id)

# Ruleset on the default branch.
gh api -X POST repos/$REPO/rulesets --input - <<JSON
{
  "name": "default-branch",
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
        "required_status_checks": [ { "context": "<ci-check-name>" } ] } },
    { "type": "required_linear_history" },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
```

Notes:
- `required_approving_review_count` is 0 so the owner can merge their own and Dependabot PRs; lit factory PRs wait for a human `APPROVED` review enforced by the factory's `merge_policy`, not by GitHub.
- The App bypass lets it squash-merge dark PRs without a review. PRs touching `.github/**` or `factory.yml` are never dark.
- If `repos/$REPO/installation` is not accessible with your token, look up the App id in GitHub Settings > Developer settings > GitHub Apps and set `APP_ID` by hand.
