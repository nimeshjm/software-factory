# Factory contracts

Shared shapes between the harness (`factory/`), the workflows (`.github/`, `scripts/`), the control plane (`control-plane/`) and the prompts (`agents/`). Change here first, then everywhere.

## Principles
- Agents propose, pipelines act. The agent process has `GEMINI_API_KEY` only (plus `HONEYCOMB_QUERY_KEY` for incident-responder). It never has a GitHub token, Cloudflare token or smoke-test token.
- Every run produces `out/result.json` and, on success, `out/proposal.json`. Success means `stop_reason == "end_turn"` and a schema-valid proposal. Anything else is a failed run with a reason.
- Deterministic policy lives in `factory/policy/` (Python, pure, unit-tested) and `control-plane/src/preflight.ts` (TS, pure, unit-tested).

## Directories at run time (GitHub Actions job)
- `$GITHUB_WORKSPACE/` is the factory checkout.
- `$GITHUB_WORKSPACE/work/` is the target repo checkout. The harness exposes it as `WORKSPACE`.
- `$GITHUB_WORKSPACE/inputs/` holds gathered inputs (read-only for the agent).
- `$GITHUB_WORKSPACE/out/` holds agent outputs.

## Harness CLI
`uv run python -m factory.run --agent <name> --phase <phase> --inputs inputs/ --out out/ [--dry-run]`
Env: `GEMINI_API_KEY`, `WORKSPACE`, `FACTORY_RUN_ID`, `OTEL_*` (optional), `HONEYCOMB_QUERY_KEY` (incident only).
Exit 0 only on success. Always writes `out/result.json`.

## inputs/*.json
- `run.json`: `{run_id, agent, phase, dry_run, trigger, target_repo, issue_number|null, pr_number|null, commit_sha}`
- `issue.json` (when issue_number): `{number, title, body, state, createdAt, author{login}, authorAssociation, labels[string], comments[{author, authorAssociation, body, createdAt}]}`
- `issues.json`: open issues `[{number, title, labels[string], state, authorAssociation, body_excerpt}]`
- `pr.json` (when pr_number): `{number, title, body, author, authorAssociation, labels[string], headRefName, baseRefName, isDraft, mergeable, files[{path, additions, deletions}], diff (string, ≤200k chars), checks[{name, status, conclusion}], reviews[{author, state}], reviewThreads[{id, isResolved, path, body}], plan_comment (string|null, body of the `<!-- factory:plan -->` comment on the linked issue)}`
- `prs.json`: open PRs `[{number, title, author, labels[string], headRefName, createdAt}]`
- `factory.json`: the target profile (`factory.yml` from the target default branch), normalised by `validate-profile --write` with all defaults filled; schema in the Target profile section below
- `deploy.json` (verifier): `{expected_sha, observed_sha|null, matched, elapsed_s, version_url}`, or `{skipped: true}` when `profile.smoke.version` is null
- `smoke.json` (verifier): `{passed, tests[{name, status, error|null}]}`
- `honeycomb.json` (verifier, incident): `{enabled, liveness{events, window_minutes}|null, errors[{signature, count, first_seen, last_seen, sample{...}}]}`
- `context/` directory: copies of the files matched by `profile.context_files` (globs allowed) from the target default branch, when present.

## out/result.json
`{run_id, agent, phase, success, stop_reason, model_calls, usage{input_tokens, output_tokens, total_tokens}, cycles, duration_s, guard_events[{guard, rule, detail, ts}], throttles, error|null}`

## out/proposal.json (per agent, Pydantic models in factory/schemas/)
- triage `TriageProposal`: `{issues[{number, labels_add[], labels_remove[], comment|null}], new_issues[{title, body, labels[], parent|null}], summary}`
- builder/bug-fixer research `ResearchArtifact`: `{research_md}` (also written to `out/research.md`)
- builder/bug-fixer plan `PlanArtifact`: `{plan_md, files[], steps[], risk_notes[], needs_human: bool}` (also `out/plan.md`)
- builder/bug-fixer implement `BuildProposal`: `{branch, title, body, files[], checks_passed: bool, notes}`
- reviewer `ReviewProposal`: `{verdict: "approve"|"request_changes"|"comment"|"needs_human", agent_checks[{name, status: "pass"|"fail"|"na", detail}], questions_for_human[], inline_comments[{path, line, body}], summary_md, ci_fix{attempted: bool, commit_message|null}}`
- verifier `VerifyProposal`: `{outcome: "pass"|"fail"|"skipped", evidence_md, bug{title, body, labels[]}|null, pr_comment|null}`
- incident-responder `IncidentProposal`: `{incidents[{signature, title, body, priority: 1|2|3, labels[], existing_issue|null}], summary}`

## Branch and title conventions
- builder: `feat/<N>-<slug>`; bug-fixer: `fix/<N>-<slug>`; `[ci-fix]` commits by reviewer post-step only.
- PR body must contain `Closes #N`. Plan comment marker: `<!-- factory:plan -->`. Review comment marker: `<!-- factory:review -->`. Verifier comment marker: `<!-- factory:verify -->`.

## Labels
See `factory.config.json#labels`. State machine: `status:backlog → status:plan-review (risk:high) → status:in-progress → status:in-review → status:done`.

## Light/dark
`autonomy:dark` iff every changed path matches `factory.json.dark_paths` and no dependency bump touches `lit_dependencies`. Else `autonomy:lit`. Changes to `factory.yml` or `.github/**` are always `lit`. Computed by `factory.policy.classify_autonomy`.

## Merge policy (`factory.policy.merge_policy`)
Merge iff: CI green, all review threads resolved, `Closes #N` for feat/fix, diff ≤ `limits.max_diff_lines` (300) and ≤ `limits.max_diff_files` (8), touched files ⊆ plan files for factory PRs, and (`autonomy:dark` or a human `APPROVED` review). Dependabot: ≤1 merge per run, cron-triggered only; the cron passes the oldest open Dependabot PR without `needs-human` as `pr` (a reviewer run without `pr` reviews nothing). A reviewer run whose target setup fails labels the PR `needs-human` and comments, without a model call.

## Control plane HTTP (bearer `FACTORY_TOKEN` unless noted)
- `POST /webhooks/github` (HMAC `GITHUB_WEBHOOK_SECRET`, no bearer)
- `POST /runs/:id/started` `{agent, phase, workflow_run_url}`
- `POST /runs/:id/finished` `{status: "success"|"failed"|"timeout"|"denied"|"lost", stop_reason, model_calls, usage, pr_number|null, issue_number|null}`
- `GET /status` (no auth) → `{paused, today{model_calls, reserved, quota}, in_flight[], last_tick{at, decisions[]}}`
- `POST /admin/pause`, `POST /admin/resume`
Dispatch: `workflow_dispatch` on the factory repo (`factory.config.json#factory`), ref `factory.ref`, workflow `<agent>.yml` with inputs `{run_id, issue, pr, phase, dry_run}` (all strings; empty when n/a).

## Workflow wrappers
`triage.yml builder.yml bug-fixer.yml reviewer.yml verifier.yml incident-responder.yml` each accept `workflow_dispatch` inputs `run_id, issue, pr, phase, dry_run` and call `agent-run.yml` with `agent: <name>`.

## Harness CLIs used by the scripts (implemented in `factory/`)
- `uv run python -m factory.schemas validate --agent A --phase P --file out/proposal.json` → exit 0 valid, 1 invalid (errors on stderr). `python -m factory.schemas dump [dir]` writes JSON schemas.
- `uv run python -m factory.guards.output_guard --file F [--file G]` → stdout `{"blocked": bool, "findings": [str]}`, exit 2 when blocked. Secret rules: the compiled-in baseline (`hcaik_`, `ghs_`, `ghp_`, `github_pat_`, `AIza`, private-key headers) plus `profile.secrets.patterns`; the `--profile` option points at `inputs/factory.json`.
- `uv run python -m factory.schemas validate-profile --file inputs/factory.json [--write]` → exit 0 valid, 1 invalid; `--write` rewrites the file normalised. Accepts YAML or JSON.
- `uv run python -m factory.policy classify-autonomy --pr inputs/pr.json --factory inputs/factory.json` → `dark|lit`.
- `uv run python -m factory.policy merge-policy --pr inputs/pr.json --factory inputs/factory.json [--plan-files out/plan_files.json] --trigger cron|webhook` → `{"merge": bool, "reasons": [str]}`.

## Stop reasons in result.json
Strands reports `tool_use` when the structured-output tool ends the loop; `factory.run` rewrites that to `end_turn`, so success is still `stop_reason == "end_turn"` plus a valid proposal. Other values: `limit_turns`, `limit_total_tokens`, `limit_output_tokens`, `quota_cap` (model-call cap), `timeout` (latency guard hard cancel or job timeout), `cancelled`, `exception`.

## Post-step rules the scripts must honour
- Triage `new_issues[].body` may contain `Depends on {child:K}` (K = 1-based index into `new_issues`); create in order and substitute real numbers.
- Issues created by verifier and incident-responder get the extra label `factory:accepted` so the control plane's author gate admits them to bug-fixer.
- Triage-created `new_issues` inherit `factory:accepted` when the parent (or, with no parent, the triaged issue) is from a trusted author or already carries `factory:accepted`; otherwise they wait for a human.
- Reviewer `ci_fix.attempted == true`: only if label `ci-fix:attempted` is absent, run `profile.toolchain.format` in `work/` (null: label `needs-human` instead), commit `[ci-fix] <commit_message>`, push, add the label.
- Builder/bug-fixer phases run in one job sharing `out/`; the harness reads `research.md`/`plan.md` from `inputs/` or `out/`.
- `honeycomb.liveness.filters` / `honeycomb.errors.filters` in the target profile are Honeycomb Query API filter objects passed through verbatim; when the profile's `honeycomb.enabled` is false the pre-step writes `{"enabled": false}`.

## Target profile (`factory.yml` in the target repo → `inputs/factory.json`)

Fetched from the target's default branch only, normalised and validated by `uv run python -m factory.schemas validate-profile --file inputs/factory.json --write` before any model call. Missing or invalid profile fails the run. Pydantic model `factory.schemas.target_profile.TargetProfile` (`extra="forbid"`, every section optional with these defaults):

```yaml
dark_paths: []                 # globs; PR is autonomy:dark only if every changed path matches
lit_dependencies: []           # dependency names whose bumps are always lit (fnmatch)
limits: { max_diff_lines: 300, max_diff_files: 8 }
toolchain:
  runtime: { node: null, python: null, go: null }   # version strings, any subset
  setup: []                    # commands run in work/ after checkout (no secrets in env)
  checks: []                   # ordered [{name, run}] executed by run_checks
  format: null                 # command used by the [ci-fix] post-step; null = never attempt
  single_test: null            # hint for prompts, e.g. "npx vitest run {file}"
  dependency_files: []         # globs; a PR touching only these is a dependency-only PR
  ignore_dirs: []              # added to the baseline skip list for list/search tools
  env_prefixes: []             # extra env prefixes passed to shell tools (never-export list wins)
protected:
  commands: []                 # regexes added to the baseline shell deny-list
  env_markers: []              # env names that must never appear in a command, e.g. EVAL_LIVE
  paths: []                    # globs the agent may never edit (baseline: .github/**, .git/**, factory.yml)
secrets:
  patterns: []                 # regexes added to the baseline secret scan
  names: []                    # variable names shown to agents as never-print
context_files: ["AGENTS.md", ".agents/*.md", "docs/product-spec.md"]   # copied to inputs/context/, in read order
risk:
  high_paths: []               # globs → risk:high
  high_keywords: []            # words/phrases in an issue → risk:high
  priority1_signals: []        # phrases that mean priority:1
  never_do: []                 # short rules shown to every agent
smoke:
  version: null                # or {path: "/version", sha_field: "sha", auth_header: "x-auth-token"}; secret is always SMOKE_AUTH_TOKEN
  paths: []                    # paths GET by the smoke test
  expect: { status: 200, content_type: null }
deploy: { workflow_name: "Deploy", branch: null }   # branch null = target default branch
honeycomb:
  enabled: false
  dataset: null
  environment: null
  liveness: { filters: [], window_minutes: 120, min_events: 1 }
  errors: { filters: [], window_minutes: 1440, group_by: [], fallback_group_by: [] }
```

Legacy `smoke.version_path: /version` is accepted and migrated to `smoke.version.path` with `auth_header: x-dashboard-token` plus a warning.

**Baseline (compiled in, profile can only add):** shell deny `git push`, `gh `, `sudo`, `curl|wget` to hosts outside `factory.config.json#allowed_hosts`, `rm -rf` outside the workspace, background `&`/`nohup`; path deny outside `WORKSPACE`, `.git/**`, `.github/**`, `factory.yml`; secret patterns `hcaik_`, `ghs_`, `ghp_`, `github_pat_`, `AIza`, private-key headers; never-export env prefixes `GEMINI`, `GITHUB_`, `GH_`, `OTEL_`, `HONEYCOMB`, `FACTORY_`, `ACTIONS_`, `RUNNER_`. `classify_autonomy` returns `lit` whenever a changed path is `factory.yml` or under `.github/`. All profile strings are capped at 400 chars, lists at 50 items.

**Prompt placeholder:** every prompt contains `{{target_profile}}`; the harness renders a `## Target profile` section there (forbidden commands and env markers, protected paths, secret names, risk criteria, priority-1 signals, never_do, context file order, check names, single-test hint, incident signature field = `honeycomb.errors.group_by[0]`).

**Secrets rename:** `SMOKE_AUTH_TOKEN` replaces `DASHBOARD_TOKEN` everywhere in the factory; the profile only names the header.

**Factory config now holds:** `target`, `factory{owner, repo, ref, app_slug}`, `allowed_hosts`, `quota`, `models`, `context`, `agents`, `honeycomb{api_base, mcp_url}`, `labels`. Dataset, filters and `enabled` live in the profile.
