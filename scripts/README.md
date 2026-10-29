# scripts/

All scripts: `set -euo pipefail`, `jq` for JSON, no secrets echoed, `shellcheck -x` clean. They share `lib.sh`.
Run from the repo root (the workflow does). Directories: `inputs/`, `out/`, `work/` (override with `INPUTS_DIR`, `OUT_DIR`, `WORK_DIR`).

| Script | Purpose | Token |
|---|---|---|
| `gather-inputs.sh json\|phases\|context\|verify\|pr` | writes `inputs/*.json` per CONTRACTS.md; `json` fetches `factory.yml` from the default branch, fails when it is missing and normalises it with `validate-profile --write`; `context` copies `profile.context_files` (globs allowed) | App token (`GH_TOKEN`) |
| `run-phases.sh` | loops the harness over `$PHASES`, copies each phase to `out/phases/<phase>/`, writes `out/phases_ok.txt`; stops after plan on `needs_human` or `risk:high` without `plan:approved` | none (minimal env) |
| `apply-proposal.sh <agent> [--auto\|phase]` | validate, output_guard, then the only GitHub mutations | fresh App token |
| `ledger-post.sh started\|finished` | control-plane ledger, never fails the job | `FACTORY_TOKEN` |
| `wait-for-deploy.sh` | polls `${TARGET_BASE_URL}${smoke.version.path}` (header `smoke.version.auth_header`, field `sha_field`, all from `inputs/factory.json`) until the field equals `EXPECTED_SHA` or 10 min, writes `inputs/deploy.json` (`{"skipped": true}` when the profile has no `smoke.version`), always exit 0 | `SMOKE_AUTH_TOKEN` (optional) |
| `honeycomb-query.sh` | Honeycomb Query Data API (`api_base` from `factory.config.json`; dataset, filters from the profile) -> `inputs/honeycomb.json` (`{enabled:false,...}` when disabled) | `HONEYCOMB_QUERY_KEY` |
| `check-config-sync.sh` | CI check: `control-plane/wrangler.jsonc` vars, `control-plane/src/config.ts` agent budgets and the `agent-run.yml` timeout map match `factory.config.json` (map entry must be >= `timeout_minutes` + 5) | none |
| `preflight-labels.sh` | idempotent `gh label create --force` for every label in `factory.config.json` | App token or PAT |

Repository variables (not secrets): `FACTORY_URL` (control plane base URL), `TARGET_BASE_URL` (deployed target, no trailing slash).

## Harness CLIs this code relies on (as built)

- `uv run python -m factory.run --agent A --phase P --inputs inputs/ --out out/` (exit 0 only on success). Phases share one `out/`, so `research.md` and `plan.md` from earlier phases stay in place. `--dry-run` is not passed by the workflow; `dry_run` only skips apply.
- `uv run python -m factory.schemas validate --agent A --phase P --file F`: exit 0 valid, exit 1 with errors on stderr.
- `uv run python -m factory.schemas validate-profile --file inputs/factory.json --write`: exit 1 on an invalid profile; `--write` rewrites the file normalised (all defaults filled).
- `uv run python -m factory.guards.output_guard --file F [--file G] --profile inputs/factory.json`: stdout `{"blocked": bool, "findings": [str]}`, exit 2 when blocked. Used on the proposal and on `git diff` of `work/`.
- `uv run python -m factory.policy classify-autonomy --pr pr.json --factory factory.json`: prints `dark` or `lit`.
- `uv run python -m factory.policy merge-policy --pr pr.json --factory factory.json [--plan-files plan_files.json] --trigger cron|webhook`: prints `{"merge": bool, "reasons": [str]}`.

Notes for the policy author:
- `pr.json.checks[].status/conclusion` are lower-cased (`completed`, `success`, `failure`; status-context `state` goes into `conclusion`). `author` is `app/<slug>` for bots (`app/<app_slug>`, `app/dependabot`).
- `plan_files.json` is extracted from a machine marker the apply step writes into the plan comment: `<!-- factory:plan-files {"files":[...]} -->`.
- `--trigger` is `cron` when the PR author is `app/dependabot` (the control plane only dispatches those from its cron), else `webhook`.
- The workflow only checks out a PR head for `reviewer`, and only when the author is `app/<app_slug>` (`factory.config.json#factory.app_slug`) or `app/dependabot` and the head is not a fork. All other agents get the target default branch.

## Behaviour worth knowing

- Agent-proposed labels are filtered to `factory.config.json#labels` and can never include `plan:approved`, `factory:accepted`, `ci-fix:attempted` or `autonomy:*` (verifier/incident issues get `factory:accepted` from the script itself).
- Triage from non-collaborators (no `factory:accepted`) gets `needs-human` only. Triage `new_issues` are created in order; `{child:K}` placeholders in bodies are replaced by the K-th created issue number. Each new issue also gets `factory:accepted` when its parent (or, with no parent, the triaged issue) is from a trusted author or already has that label, so the App-authored child passes the control plane's author gate.
- All text posted to GitHub has `@mentions` neutralised (zero-width space after `@`).
- `implement` aborts unless the branch matches `^(feat|fix)/[0-9]+-[a-z0-9-]+$`, matches the agent and issue, is the checked-out branch, has commits, and the diff does not touch `.github/**` or `factory.yml`. PR is a draft when `checks_passed` is false. The PR number is written to `out/created-pr.txt` for the ledger.
- `implement` spike path: when `files` is empty and `findings_md` is non-empty (builder only, and only when the profile's `web.allowed_hosts` is non-empty, and only when `out/phases/implement/result.json` `tool_calls` shows at least one `fetch_url` with a non-null `http_status`, else it dies with "spike findings without any fetched source"), apply runs before any branch checks: it upserts a `<!-- factory:findings -->` issue comment (sanitised findings + run id), sets `status:done` (removing `status:in-progress`, `status:plan-review`, `status:backlog`) and closes the issue as completed (skipped if already closed). No push, no PR, no `created-pr.txt`, so no reviewer run; reruns update the same comment. Empty `files` without `findings_md` still fails with "no commits ahead".
- `ci_fix.attempted`: only on factory branches and only when `ci-fix:attempted` is absent: runs `profile.toolchain.format` in `work/` (with no GitHub tokens in env; when the profile has none, labels `needs-human`), commits `[ci-fix] ...`, pushes, labels. Otherwise `needs-human`.
- `ledger-post.sh finished` aggregates `out/phases/*/result.json`; with none it reports `timeout` (or `denied` when the author gate failed).
