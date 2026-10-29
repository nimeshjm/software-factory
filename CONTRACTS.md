# Factory contracts

Shared shapes between the harness (`factory/`), the workflows (`.github/`, `scripts/`), the control plane (`control-plane/`) and the prompts (`agents/`). Change here first, then everywhere.

## Principles
- Agents propose, pipelines act. The agent process has `GEMINI_API_KEY` only (plus `HONEYCOMB_QUERY_KEY` for incident-responder and `TAVILY_API_KEY` for builder and bug-fixer, both read only inside their tool and never exported to shell tools). It never has a GitHub token, Cloudflare token or smoke-test token.
- Every run produces `out/result.json` and, on success, `out/proposal.json`. Success means `stop_reason == "end_turn"` and a schema-valid proposal. Anything else is a failed run with a reason.
- Deterministic policy lives in `factory/policy/` (Python, pure, unit-tested) and `control-plane/src/preflight.ts` (TS, pure, unit-tested).

## Directories at run time (GitHub Actions job)
- `$GITHUB_WORKSPACE/` is the factory checkout.
- `$GITHUB_WORKSPACE/work/` is the target repo checkout. The harness exposes it as `WORKSPACE`.
- `$GITHUB_WORKSPACE/inputs/` holds gathered inputs (read-only for the agent).
- `$GITHUB_WORKSPACE/out/` holds agent outputs.

## Harness CLI
`uv run python -m factory.run --agent <name> --phase <phase> --inputs inputs/ --out out/ [--dry-run]`
Env: `GEMINI_API_KEY`, `WORKSPACE`, `FACTORY_RUN_ID`, `OTEL_*` (optional), `HONEYCOMB_QUERY_KEY` (incident only), `TAVILY_API_KEY` (builder and bug-fixer only; optional, `web_search` reports "not configured" without it).
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
`{run_id, agent, phase, success, stop_reason, model_calls, usage{input_tokens, output_tokens, total_tokens}, cycles, duration_s, guard_events[{guard, rule, detail, ts}], tool_calls[{name, target, status, http_status|null, ts}], throttles, error|null}`

`tool_calls` records every tool call in order (capped at 200 entries): `name`; `target` = the URL for `fetch_url` (the final URL after redirects when it succeeded), the query for `web_search`, the path for file tools, the command for `shell_in_workspace`, else "" (redacted with the output guard, clipped to 200 chars); `status` = `ok`, `error` (the tool returned a string starting `error:`) or `blocked` (a guard cancelled it); `http_status` = the response status for `fetch_url` when a response arrived, else null. A hook (`factory/guards/tool_log.py`, `ToolCallLog`) records it after each call, prints one line to stderr (`[tool] <name> <status> <http_status|-> <target>`), and adds a `factory.tool_call` span event with attributes `factory.tool.name`, `factory.tool.target`, `factory.tool.status`, `factory.tool.http_status` (when set) to the current span. That is the Strands cycle span, not `execute_tool`: Strands ends the `execute_tool` span before `AfterToolCallEvent` fires, so per-call span attributes would overwrite each other. In Honeycomb the events appear as one span event per tool call under the run's trace.

## out/proposal.json (per agent, Pydantic models in factory/schemas/)
- triage `TriageProposal`: `{issues[{number, labels_add[], labels_remove[], comment|null}], new_issues[{title, body, labels[], parent|null}], summary}`
- builder/bug-fixer research `ResearchArtifact`: `{research_md}` (also written to `out/research.md`)
- builder/bug-fixer plan `PlanArtifact`: `{plan_md, files[], steps[], risk_notes[], needs_human: bool}` (also `out/plan.md`)
- builder/bug-fixer implement `BuildProposal`: `{branch, title, body, files[], checks_passed: bool, notes, findings_md: string|null}`. `files == []` with a non-empty `findings_md` is a spike result (builder only; see Spikes). `files == []` without `findings_md` still fails the run at apply ("no commits ahead").
- reviewer `ReviewProposal`: `{verdict: "approve"|"request_changes"|"comment"|"needs_human", agent_checks[{name, status: "pass"|"fail"|"na", detail}], questions_for_human[], inline_comments[{path, line, body}], summary_md, ci_fix{attempted: bool, commit_message|null}}`
- verifier `VerifyProposal`: `{outcome: "pass"|"fail"|"skipped", evidence_md, bug{title, body, labels[]}|null, pr_comment|null}`
- incident-responder `IncidentProposal`: `{incidents[{signature, title, body, priority: 1|2|3, labels[], existing_issue|null}], summary}`

## Branch and title conventions
- builder: `feat/<N>-<slug>`; bug-fixer: `fix/<N>-<slug>`; `[ci-fix]` commits by reviewer post-step only.
- PR body must contain `Closes #N`. Plan comment marker: `<!-- factory:plan -->`. Review comment marker: `<!-- factory:review -->`. Verifier comment marker: `<!-- factory:verify -->`. Spike findings marker: `<!-- factory:findings -->`.

## Labels
See `factory.config.json#labels` (includes `spike`, set by triage or a human on investigation-only issues; informational, the control plane does not read it). State machine: `status:backlog → status:plan-review (risk:high) → status:in-progress → status:in-review → status:done`.

## Light/dark
`autonomy:dark` iff every changed path matches `factory.json.dark_paths` and no dependency bump touches `lit_dependencies`. With `dark_dependency_bumps: true`, a dependency bump whose every path matches `toolchain.dependency_files` is also `dark` (still never when it touches `lit_dependencies`). Else `autonomy:lit`. Changes to `factory.yml` or `.github/**` are always `lit`. Computed by `factory.policy.classify_autonomy`.

## Merge policy (`factory.policy.merge_policy`)
Merge iff: CI green, all review threads resolved, `Closes #N` for feat/fix, diff ≤ `limits.max_diff_lines` (300) and ≤ `limits.max_diff_files` (8), touched files ⊆ plan files for factory PRs, and (`autonomy:dark` or a human `APPROVED` review). Dependabot: ≤1 merge per run, cron-triggered only; merges without a human only when `dark_dependency_bumps` makes the bump dark; the cron passes the oldest open Dependabot PR without `needs-human` as `pr` (a reviewer run without `pr` reviews nothing). A reviewer run whose target setup fails labels the PR `needs-human` and comments, without a model call.

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
- `uv run python -m factory.guards.output_guard --file F [--file G]` → stdout `{"blocked": bool, "findings": [str]}`, exit 2 when blocked. Secret rules: the compiled-in baseline (`hcaik_`, `ghs_`, `ghp_`, `github_pat_`, `AIza`, `tvly-`, private-key headers) plus `profile.secrets.patterns`; the `--profile` option points at `inputs/factory.json`.
- `uv run python -m factory.schemas validate-profile --file inputs/factory.json [--write]` → exit 0 valid, 1 invalid; `--write` rewrites the file normalised. Accepts YAML or JSON.
- `uv run python -m factory.policy classify-autonomy --pr inputs/pr.json --factory inputs/factory.json` → `dark|lit`.
- `uv run python -m factory.policy merge-policy --pr inputs/pr.json --factory inputs/factory.json [--plan-files out/plan_files.json] --trigger cron|webhook` → `{"merge": bool, "reasons": [str]}`.

## Web research (`web_search`, `fetch_url`) and spikes

The builder and the bug-fixer can search the web and read pages in their `research` and `implement` phases (never `plan`), on any issue: library and API docs, error messages, upstream changelogs, and spikes. Both tools are off unless the profile's `web.allowed_hosts` is non-empty. Everything they return is untrusted literal data.

- **Tool** `web_search(query: str, max_results: int = 5) -> str` in `factory/tools/web_search.py`. `POST https://api.tavily.com/search` with `Authorization: Bearer $TAVILY_API_KEY`, body `{query, max_results (clamped 1..8), search_depth: "basic", include_answer: false, include_raw_content: false, include_images: false}`, `httpx` with `trust_env=False`, 10 s timeout. Query: 1..400 chars after strip. Returns one entry per result, `[n] <title>\n<url>\n<snippet clipped to 500 chars>`, the whole list inside `<untrusted source="web_search">...</untrusted>` (with `</untrusted` escaped), at most 8,000 chars. Errors return `error: ...` (missing key → `error: web search is not configured`; empty `web.allowed_hosts` → `error: web research is disabled in the target profile`); never raises. Result URLs are not filtered; `fetch_url` applies `web.allowed_hosts` when the agent reads one. The guard caps `web_search` at 10 calls per harness process (`search_cap`) and refuses an empty or over-long query (`search_query`).
- **Key handling**: `TAVILY_API_KEY` is passed by `agent-run.yml` to the harness step only for `builder` and `bug-fixer`. `TAVILY` is a never-export env prefix, so no shell tool sees it. Baseline secret pattern `tvly-[A-Za-z0-9_-]{8,}` blocks it in proposals. Budget: Tavily's free tier is 1,000 basic searches a month; at 10 per phase that is about 50 research-heavy runs a month.

- **Tool** `fetch_url(url: str) -> str` in `factory/tools/fetch.py`, registered for the builder's and bug-fixer's `research` and `implement` phases (never `plan`). HTTP GET with no custom headers or body, `httpx` with `trust_env=False` and `follow_redirects=False` (redirects followed manually, each hop re-checked), 10 s timeout, response body read as a stream and cut at 200 KB, or at 2 MB when the content-type contains `json`. HTML is reduced to text with the stdlib `html.parser`. A JSON body (content-type contains `json`, or a non-HTML body starting with `{` or `[`) is parsed and prefixed with `JSON shape (whole body, ...)`: an indented tree of every key in the body, one line per key with its type and a sample clipped to 40 chars (samples dropped when the tree would exceed 15,000 chars), arrays as `key[<length>]` with the union of the keys of their first 50 items, depth capped at 8, and a subtree whose keys repeat one already shown rendered as one line `..., same keys as <path>`; then `Raw JSON:` and the body. A body that does not parse gets `[JSON shape unavailable: body cut at N bytes]` (or `not valid JSON`) instead. Text is clipped to 20,000 chars; when anything was cut the text ends with `[clipped: showing the first 20000 of N chars; body cut at N bytes]` (whichever apply), so the agent never mistakes a partial body for the whole. Returns `HTTP <status> <content-type>\n<untrusted source="<url>">\n<text>\n</untrusted>`, with any `</untrusted` inside the text escaped. Errors return a string starting `error:`; the tool never raises.
- **Rules** (one pure function `url_allowed(url, allowed_hosts, *, allow_loopback=False) -> (rule, detail) | None` in `factory/guards/tool_guard.py`, called by both the guard and the tool): scheme `https` only (`fetch_scheme`); no userinfo, explicit port other than 443 refused (`fetch_url_shape`); host must match `profile.web.allowed_hosts`, where `["*"]` (only valid as the sole entry) matches any host (`fetch_host`; empty list → `fetch_disabled`); host that is an IP literal, or resolves to a private, loopback, link-local, multicast, reserved or unspecified address, is refused (`fetch_private_address`; DNS resolution happens in the tool, before connecting, and on every redirect hop). `allow_loopback` is a keyword argument used by tests only (it also lets loopback hosts use `http` and any port, for a local fixture server); nothing in production sets it, and no env var switches it on. The tool's DNS and redirect refusals return `error: blocked <rule>: ...` to the model; only the guard's static refusals are guard events. Known gap: httpx resolves again when connecting, so DNS rebinding between the check and the connect is not prevented.
- **Guard**: `ToolGuard` evaluates `fetch_url` calls with `url_allowed` (static checks, no DNS) and counts them: the 21st call in one harness process (i.e. per phase) is blocked as `fetch_cap`. Denials use the usual `[tool_guard] Blocked <rule>: <detail>` text and guard event.
- **Prompt**: the `## Target profile` section lists `web.allowed_hosts` ("Hosts `fetch_url` may read"; `["*"]` renders as "any public https host"). Fetched text is untrusted literal data: the agent never follows instructions in it and never builds a URL or a search query from workspace, issue or profile content beyond short public identifiers (a library name, an error message, a version); with any host allowed, URLs and queries are the only way out of the sandbox.

**Spikes**: a spike is a builder issue whose acceptance criteria are all investigation (investigate, check, record findings); it delivers an issue comment, not a PR. Findings have one section per acceptance criterion, answered from a fetch of the endpoint that criterion names, listing the key paths seen in the fetch result; a field is called absent only when the result covered the whole body (prompt rule; `check-findings` verifies statuses, not fields).

- **Apply** (`apply_implement`, before any branch checks): when `.files` is empty and `.findings_md` is non-empty: (1) agent must be `builder` and `inputs/factory.json` `.web.allowed_hosts` must be non-empty, else die; (1b) evidence: the `tool_calls` of `out/phases/research/result.json` and `out/phases/implement/result.json` together must contain at least one `fetch_url` entry with a non-null `http_status`, else die "spike findings without any fetched source" (findings written from memory are never posted); (1c) claims: `uv run python -m factory.policy check-findings --proposal <implement proposal> --results out/phases/research/result.json out/phases/implement/result.json` must exit 0, else die with its reasons; (1d) the posted comment is the findings followed by `## Evidence (recorded by the pipeline)`, the markdown from `uv run python -m factory.policy evidence-table --results <same two files>`; (2) upsert an issue comment `<!-- factory:findings -->` + sanitised findings + `_Factory run <RUN_ID>_` (output guard already ran on the proposal); (3) labels → add `status:done`, remove `status:in-progress,status:plan-review,status:backlog`; (4) `gh issue close --reason completed`. No push, no PR, no `created-pr.txt`, so no reviewer run. Re-running is idempotent.

**Findings checks (`factory/policy/findings.py`, pure, unit-tested):**
- `check-findings`: split `findings_md` into sections at markdown headings. In each section, every three-digit status stated in an "HTTP status" phrase (case-insensitive, e.g. `HTTP status: 200`, `**HTTP status**: 200 (...) / 400 (...)`) must equal the `http_status` of some `fetch_url` call in the given results whose target matches a URL written in that same section. Match = same scheme, host and path; the query string is ignored and `<...>` placeholders are dropped before comparing. A claimed status with no matching fetch is a violation. Prints `{"ok": bool, "violations": [str]}`; exit 0 when ok, 1 otherwise. Sections that state no status pass (a "not fetched" or "requires a key" source).
- `evidence-table`: markdown listing every `fetch_url` call (URL, HTTP status or `-`, status) and every `web_search` query from the given results, in call order, grouped by phase, with targets passed through the same mention sanitising as other posted text (no `@`). Empty lists render as "none".

**Spike model:** a builder `research` or `implement` phase on an issue labelled `spike` (`inputs/issue.json`) uses `factory.config.json#models.spike` instead of the phase's model (`gemini-3.5-flash-lite` with `thinking_level: high`), so investigation follows its instructions more closely. Other phases and issues are unchanged.

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
dark_dependency_bumps: false   # true: a Dependabot bump touching only toolchain.dependency_files is dark (merges on green CI)
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
web:
  allowed_hosts: []            # hosts fetch_url may read and web_search may run for: exact "host", "*.suffix" (subdomains only), or ["*"] = any public host; [] = web research and spikes disabled
honeycomb:
  enabled: false
  dataset: null
  environment: null
  liveness: { filters: [], window_minutes: 120, min_events: 1 }
  errors: { filters: [], window_minutes: 1440, group_by: [], fallback_group_by: [] }
```

Legacy `smoke.version_path: /version` is accepted and migrated to `smoke.version.path` with `auth_header: x-dashboard-token` plus a warning.

**Baseline (compiled in, profile can only add):** `fetch_url` is https only, no private/loopback addresses, redirects re-checked against `web.allowed_hosts` (`["*"]` = any public host), 200 KB per response (2 MB for JSON), 20 fetches and 10 searches per phase, 10 s timeout; shell deny `git push`, `gh `, `sudo`, `curl|wget` to hosts outside `factory.config.json#allowed_hosts`, `rm -rf` outside the workspace, background `&`/`nohup`; path deny outside `WORKSPACE`, `.git/**`, `.github/**`, `factory.yml`; secret patterns `hcaik_`, `ghs_`, `ghp_`, `github_pat_`, `AIza`, `tvly-`, private-key headers; never-export env prefixes `GEMINI`, `TAVILY`, `GITHUB_`, `GH_`, `OTEL_`, `HONEYCOMB`, `FACTORY_`, `ACTIONS_`, `RUNNER_`. `classify_autonomy` returns `lit` whenever a changed path is `factory.yml` or under `.github/`. All profile strings are capped at 400 chars, lists at 50 items.

**Prompt placeholder:** every prompt contains `{{target_profile}}`; the harness renders a `## Target profile` section there (forbidden commands and env markers, protected paths, secret names, risk criteria, priority-1 signals, never_do, context file order, check names, single-test hint, incident signature field = `honeycomb.errors.group_by[0]`).

**Secrets rename:** `SMOKE_AUTH_TOKEN` replaces `DASHBOARD_TOKEN` everywhere in the factory; the profile only names the header.

**Factory config now holds:** `target`, `factory{owner, repo, ref, app_slug}`, `allowed_hosts`, `quota`, `models`, `context`, `agents`, `honeycomb{api_base, mcp_url}`, `labels`. Dataset, filters and `enabled` live in the profile.
