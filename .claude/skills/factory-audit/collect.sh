#!/usr/bin/env bash
# Read-only collector for the factory-audit skill.
# Gathers the last N hours of factory activity (workflow runs, run artifacts,
# control plane status, target issue and PR movement) and prints a Markdown
# report on stdout. It never mutates GitHub or the control plane.
#
# Usage: .claude/skills/factory-audit/collect.sh [--hours N] [--no-artifacts] [--no-logs]
# Needs: gh (logged in, read access to both repos), jq, curl, git.
set -euo pipefail

HOURS=24
WITH_ARTIFACTS=1
WITH_LOGS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --hours) HOURS="$2"; shift 2 ;;
    --no-artifacts) WITH_ARTIFACTS=0; shift ;;
    --no-logs) WITH_LOGS=0; shift ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for tool in gh jq curl git; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 2; }
done

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
CONFIG="$ROOT/factory.config.json"
[ -f "$CONFIG" ] || { echo "no factory.config.json at $ROOT" >&2; exit 2; }

FACTORY_REPO="$(jq -r '.factory.owner + "/" + .factory.repo' "$CONFIG")"
TARGET_REPO="$(jq -r '.target.owner + "/" + .target.repo' "$CONFIG")"
APP_SLUG="$(jq -r '.factory.app_slug' "$CONFIG")"
QUOTA="$(jq -r '.quota.daily_model_calls' "$CONFIG")"
MAX_PRS="$(jq -r '.quota.max_open_factory_prs' "$CONFIG")"
FACTORY_URL="${FACTORY_URL:-$(gh variable get FACTORY_URL -R "$FACTORY_REPO" 2>/dev/null || true)}"

NOW_EPOCH="$(date +%s)"
SINCE_EPOCH=$(( NOW_EPOCH - HOURS * 3600 ))
fmt_utc() { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
NOW="$(fmt_utc "$NOW_EPOCH")"
SINCE="$(fmt_utc "$SINCE_EPOCH")"

SCRATCH="${AUDIT_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/factory-audit.XXXXXX")}"
mkdir -p "$SCRATCH/logs" "$SCRATCH/artifacts"

# Lines that GitHub Actions prints around every step and that carry no signal.
NOISE='Post job cleanup|Token revoked|git config|git version|git submodule|Temporarily overriding HOME|Adding repository directory|Cleaning up orphan|Node.js 20 is deprecated|DeprecationWarning|trace-deprecation|##\[endgroup\]|extraheader|^[[:space:]]*$'

strip_cols() { sed 's/^[^\t]*\t[^\t]*\t//; s/^[0-9T:.Z-]\{20,30\} //'; }

echo "# Factory audit: last ${HOURS}h"
echo
echo "- window: \`$SINCE\` to \`$NOW\` (UTC)"
echo "- factory: \`$FACTORY_REPO\`, target: \`$TARGET_REPO\`, app: \`app/$APP_SLUG\`"
echo "- scratch: \`$SCRATCH\` (logs and artifacts kept for follow-up)"
echo

# ---------------------------------------------------------------- control plane
echo "## Control plane"
echo
if [ -n "$FACTORY_URL" ] && STATUS="$(curl -sf --max-time 20 "$FACTORY_URL/status")"; then
  echo "$STATUS" > "$SCRATCH/status.json"
  echo '```json'; echo "$STATUS" | jq -c '.'; echo '```'
  echo
  echo "$STATUS" | jq -r --argjson q "$QUOTA" '
    "- paused: \(.paused)",
    "- model calls today: \(.today.model_calls) of \(.today.quota) (config says \($q)), reserved \(.today.reserved)",
    "- in flight: \(.in_flight | length)",
    (.in_flight[]? | "  - \(.run_id) \(.agent) \(.subject) status=\(.status) dispatched=\(.dispatched_at) started=\(.started_at // "-") \(.workflow_run_url // "")"),
    "- last tick: \(.last_tick.at // "-")",
    (.last_tick.decisions[]? | "  - \(.kind) \(.agent): \(.reason // .subject // "")")'
  echo
  echo "$STATUS" | jq -r '.in_flight[]? | select((.dispatched_at | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601) < (now - 3600))
    | "STALL: in-flight run \(.run_id) (\(.agent) \(.subject)) dispatched more than an hour ago"'
  if [ "$(echo "$STATUS" | jq -r '.paused')" = "true" ]; then echo "STALL: control plane is paused"; fi
else
  echo "STALL: control plane /status unreachable (FACTORY_URL='${FACTORY_URL:-unset}')"
fi
echo

# ---------------------------------------------------------------- factory commits
echo "## Factory commits in window"
echo
# shellcheck disable=SC2016
if git -C "$ROOT" log --since="$SINCE" --format='- `%h` %ci %s' | grep . ; then :; else echo "- none"; fi
echo "- working tree: $(git -C "$ROOT" status --porcelain | wc -l | tr -d ' ') uncommitted path(s)"
echo

# ---------------------------------------------------------------- workflow runs
echo "## Workflow runs in window"
echo
gh run list -R "$FACTORY_REPO" --limit 300 \
  --json databaseId,workflowName,status,conclusion,createdAt,updatedAt,event,displayTitle,url \
  --jq "[.[] | select(.createdAt > \"$SINCE\")] | sort_by(.createdAt)" > "$SCRATCH/runs.json"

RUN_COUNT="$(jq 'length' "$SCRATCH/runs.json")"
if [ "$RUN_COUNT" = "0" ]; then
  echo "STALL: no workflow runs at all in the window"
  echo
else
  echo "Counts by workflow and conclusion:"
  echo
  echo "| workflow | conclusion | runs |"; echo "|---|---|---|"
  jq -r 'group_by(.workflowName, .conclusion)[] | "| \(.[0].workflowName) | \(.[0].conclusion // .[0].status) | \(length) |"' "$SCRATCH/runs.json"
  echo
  echo "| created (UTC) | run | workflow | event | conclusion | title |"; echo "|---|---|---|---|---|---|"
  jq -r '.[] | "| \(.createdAt) | \(.databaseId) | \(.workflowName) | \(.event) | \(.conclusion // .status) | \(.displayTitle | .[0:70]) |"' "$SCRATCH/runs.json"
  echo
  jq -r '.[] | select(.conclusion != "success" and .conclusion != "skipped") | "STALL: run \(.databaseId) (\(.workflowName), \(.createdAt)) ended \(.conclusion // .status)"' "$SCRATCH/runs.json"
  echo
fi

# ---------------------------------------------------------------- per-run detail
if [ "$WITH_LOGS" = "1" ] && [ "$RUN_COUNT" != "0" ]; then
  echo "## Agent runs: inputs, models, apply outcome"
  echo
  echo "Per run: dispatch inputs, model switches, tool errors, the apply step's output, and the failed step for runs that did not succeed."
  echo
  while IFS=$'\t' read -r id wf concl created event; do
    [ "$wf" = "ci" ] && continue
    log="$SCRATCH/logs/$id.log"
    if ! gh run view "$id" -R "$FACTORY_REPO" --log > "$log" 2>/dev/null; then
      echo "### $id $wf ($concl, $created, $event)"; echo; echo "- log unavailable (expired, in progress, or startup failure with no job)"; echo
      continue
    fi
    inputs="$(strip_cols < "$log" | grep -E '^\s+(issue|pr|dry_run|phase|run_id):' | sed 's/^ *//' | sort -u | tr '\n' ' ' || true)"
    trigger="$(strip_cols < "$log" | grep -E '^\s+TRIGGER:' | head -1 | sed 's/^ *//' || true)"
    models="$(strip_cols < "$log" | grep -E '^\[model\]' | sort -u | head -5 || true)"
    tools_ok="$(strip_cols < "$log" | grep -cE '^\[tool\] [A-Za-z_]+ ok' || true)"
    tools_err="$(strip_cols < "$log" | grep -E '^\[tool\] [A-Za-z_]+ error' | sed 's/^/    /' | sort | uniq -c | sort -rn | head -5 || true)"
    echo "### $id $wf ($concl, $created, $event)"
    echo
    echo "- inputs: ${inputs:-none found} ${trigger}"
    echo "- tool calls ok: $tools_ok"
    [ -n "$tools_err" ] && { echo "- tool errors:"; echo "$tools_err"; }
    [ -n "$models" ] && { echo "- model events:"; printf '    %s\n' "$models"; }
    apply="$(strip_cols < "$log" | awk '/\[group\]Run scripts\/apply-proposal.sh/{f=1; next} f && /##\[group\]/{exit} f' | grep -vE "$NOISE" | grep -vE '^\^\[\[36;1m|^shell: |^env:$|^  [A-Z_]+: |RuntimeWarning|<frozen runpy>' | head -40 || true)"
    if [ -n "$apply" ]; then echo "- apply step:"; echo '```'; echo "$apply"; echo '```'; fi
    if [ "$concl" != "success" ]; then
      failed_steps="$(gh run view "$id" -R "$FACTORY_REPO" --json jobs --jq '.jobs[] | .steps[] | select(.conclusion == "failure") | .name' 2>/dev/null | tr '\n' ',' | sed 's/,$//' || true)"
      echo "- failed steps: ${failed_steps:-none recorded (startup failure or cancelled before any step)}"
      failed_log="$(strip_cols < "$log" | grep -vE "$NOISE" | grep -vE '^\^\[\[36;1m|^shell: |^env:$|^  [A-Za-z_-]+: |^with:$|RuntimeWarning|<frozen runpy>' | grep -B20 -m1 '##\[error\]' || true)"
      if [ -n "$failed_log" ]; then echo "- lines before the first error marker:"; echo '```'; echo "$failed_log"; echo '```'; fi
    fi
    echo
  done < <(jq -r '.[] | [.databaseId, .workflowName, (.conclusion // .status), .createdAt, .event] | @tsv' "$SCRATCH/runs.json")
fi

# ---------------------------------------------------------------- artifacts
if [ "$WITH_ARTIFACTS" = "1" ] && [ "$RUN_COUNT" != "0" ]; then
  echo "## Run artifacts (result.json per phase)"
  echo
  echo "Artifacts expire after one day; a missing row means it was gone or the run never uploaded one."
  echo
  echo "| run | workflow | phase | success | stop_reason | models used | model calls | total tokens | tool calls (errors) |"
  echo "|---|---|---|---|---|---|---|---|---|"
  while IFS=$'\t' read -r id wf; do
    [ "$wf" = "ci" ] && continue
    dir="$SCRATCH/artifacts/$id"
    if ! gh run download "$id" -R "$FACTORY_REPO" -D "$dir" >/dev/null 2>&1; then
      echo "| $id | $wf | - | - | no artifact | | | | |"
      continue
    fi
    found=0
    while IFS= read -r rj; do
      found=1
      phase="$(basename "$(dirname "$rj")")"
      jq -r --arg id "$id" --arg wf "$wf" --arg ph "$phase" '
        "| \($id) | \($wf) | \($ph) | \(.success // "?") | \(.stop_reason // "?") | \((.model_ids_used // []) | join(", ")) | \(.model_calls // "?") | \(.usage.total_tokens // "?") | \((.tool_calls // []) | length) (\([.tool_calls[]? | select(.error != null and .error != "")] | length)) |"' "$rj"
    done < <(find "$dir" -path '*phases/*/result.json' | sort)
    [ "$found" = "0" ] && echo "| $id | $wf | - | - | artifact without result.json | | | | |"
    pr_file="$(find "$dir" -name created-pr.txt | head -1)"
    [ -n "$pr_file" ] && echo "| $id | $wf | created PR | #$(cat "$pr_file") | | | | | |"
  done < <(jq -r '.[] | [.databaseId, .workflowName] | @tsv' "$SCRATCH/runs.json")
  echo
fi

# ---------------------------------------------------------------- target issues
echo "## Target issues updated in window"
echo
gh issue list -R "$TARGET_REPO" --state all --limit 100 \
  --json number,title,state,labels,updatedAt,closedAt,author \
  --jq "[.[] | select(.updatedAt > \"$SINCE\")] | sort_by(.number)" > "$SCRATCH/issues.json"
if [ "$(jq 'length' "$SCRATCH/issues.json")" = "0" ]; then echo "- none"; echo; else
  while IFS=$'\x1f' read -r n state title labels author; do
    echo "### #$n [$state] $title"
    echo
    echo "- author: $author, labels: $labels"
    gh api "repos/$TARGET_REPO/issues/$n/timeline" --paginate 2>/dev/null \
      | jq -r --arg since "$SINCE" '.[] | select(.created_at != null and .created_at > $since)
          | "- \(.created_at) \(.actor.login // .user.login // "?") \(.event): \(.label.name // (.body // "" | .[0:160] | gsub("\n"; " ")))"' \
      || echo "- (timeline unavailable)"
    echo
  done < <(jq -r '.[] | [.number, .state, .title, ([.labels[].name] | join(",")), .author.login] | map(tostring | gsub("\u001f"; " ")) | join("\u001f")' "$SCRATCH/issues.json")
fi

# ---------------------------------------------------------------- target PRs
echo "## Target PRs updated in window"
echo
gh pr list -R "$TARGET_REPO" --state all --limit 100 \
  --json number,title,state,labels,updatedAt,mergedAt,closedAt,author,headRefName,reviewDecision,isDraft,mergeable,statusCheckRollup,url \
  --jq "[.[] | select(.updatedAt > \"$SINCE\")] | sort_by(.number)" > "$SCRATCH/prs.json"
if [ "$(jq 'length' "$SCRATCH/prs.json")" = "0" ]; then echo "- none"; echo; else
  while IFS=$'\x1f' read -r n state title author head labels decision draft mergeable merged; do
    echo "### PR #$n [$state] $title"
    echo
    echo "- author: $author, branch: \`$head\`, labels: $labels, draft: $draft, mergeable: $mergeable, review decision: $decision, merged: $merged"
    jq -r --argjson n "$n" '.[] | select(.number == $n) | .statusCheckRollup | if length == 0 then "- checks: none" else "- checks: " + ([.[] | "\(.name // .context)=\(.conclusion // .state)"] | join(", ")) end' "$SCRATCH/prs.json"
    gh api "repos/$TARGET_REPO/pulls/$n/reviews" 2>/dev/null \
      | jq -r --arg since "$SINCE" '.[] | select(.submitted_at > $since) | "- review \(.submitted_at) \(.user.login) \(.state): \(.body | .[0:140] | gsub("\n"; " "))"' || true
    gh api "repos/$TARGET_REPO/issues/$n/comments" 2>/dev/null \
      | jq -r --arg since "$SINCE" '.[] | select(.created_at > $since) | "- comment \(.created_at) \(.user.login): \(.body | .[0:140] | gsub("\n"; " "))"' || true
    if [ "$state" = "OPEN" ]; then
      unresolved="$(gh api graphql -f query="{ repository(owner:\"${TARGET_REPO%/*}\", name:\"${TARGET_REPO#*/}\") { pullRequest(number:$n) { reviewThreads(first:50) { nodes { isResolved } } } } }" \
        --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length' 2>/dev/null || echo "?")"
      echo "- unresolved review threads: $unresolved"
      human_approved="$(gh api "repos/$TARGET_REPO/pulls/$n/reviews" 2>/dev/null | jq -r '[.[] | select(.state == "APPROVED" and (.user.login | endswith("[bot]") | not))] | length')"
      checks_bad="$(jq -r --argjson n "$n" '.[] | select(.number == $n) | [.statusCheckRollup[] | select((.conclusion // .state) | test("SUCCESS|NEUTRAL|SKIPPED") | not)] | length' "$SCRATCH/prs.json")"
      if [ "${human_approved:-0}" != "0" ] && [ "$checks_bad" = "0" ] && [ "$unresolved" = "0" ] && [ "$mergeable" = "MERGEABLE" ]; then
        echo "STALL: PR #$n has a human APPROVED review, green checks, no open threads and is mergeable, but is still open. A review does not trigger the reviewer (only opened, synchronize, check_suite and the Dependabot cron do), so nothing will merge it until the next event."
      fi
    fi
    echo
  done < <(jq -r '.[] | [.number, .state, .title, .author.login, .headRefName, ([.labels[].name] | join(",")), (.reviewDecision // "none"), .isDraft, (.mergeable // "UNKNOWN"), (.mergedAt // "no")] | map(tostring | gsub("\u001f"; " ")) | join("\u001f")' "$SCRATCH/prs.json")
fi

# ---------------------------------------------------------------- backlog and gates
echo "## Backlog state and gates (now)"
echo
gh issue list -R "$TARGET_REPO" --state open --limit 200 --json number,title,labels \
  --jq '[.[] | {n: .number, t: .title, l: [.labels[].name]}]' > "$SCRATCH/open-issues.json"
jq -r '
  def has($x): .l | index($x) != null;
  "- open issues: \(length)",
  "- status:backlog: \([.[] | select(has("status:backlog")) | "#\(.n)"] | join(" "))",
  "- status:in-progress: \([.[] | select(has("status:in-progress")) | "#\(.n)"] | join(" "))",
  "- status:in-review: \([.[] | select(has("status:in-review")) | "#\(.n)"] | join(" "))",
  "- status:plan-review: \([.[] | select(has("status:plan-review")) | "#\(.n)"] | join(" "))",
  "- needs-human: \([.[] | select(has("needs-human")) | "#\(.n)"] | join(" "))",
  "- risk:high without plan:approved: \([.[] | select(has("risk:high") and (has("plan:approved") | not)) | "#\(.n)"] | join(" "))",
  "- unlabelled (triage pending): \([.[] | select(.l | length == 0) | "#\(.n)"] | join(" "))",
  "- bugs_first blockers (open bug, priority:1 or 2, not needs-human): \([.[] | select(has("bug") and (has("priority:1") or has("priority:2")) and (has("needs-human") | not)) | "#\(.n)"] | join(" "))"
' "$SCRATCH/open-issues.json"
open_factory_prs="$(gh pr list -R "$TARGET_REPO" --state open --limit 100 --json number,author --jq "[.[] | select(.author.login == \"app/$APP_SLUG\" or .author.login == \"$APP_SLUG\" or (.author.login | startswith(\"$APP_SLUG\")))] | length")"
echo "- open factory PRs: $open_factory_prs of max $MAX_PRS"
echo
# Issues in progress with no run on them in the window.
while IFS= read -r n; do
  if ! grep -qE "issue: ?$n( |$)" "$SCRATCH"/logs/*.log 2>/dev/null; then
    echo "STALL: issue #$n is status:in-progress but no run in the window carried issue=$n"
  fi
done < <(jq -r '.[] | select(.l | index("status:in-progress") != null) | .n' "$SCRATCH/open-issues.json")
# Issues in review whose PR is no longer open.
while IFS= read -r n; do
  open_pr="$(gh pr list -R "$TARGET_REPO" --state open --limit 100 --json number,headRefName --jq "[.[] | select(.headRefName | test(\"^(feat|fix)/$n-\"))] | length")"
  [ "$open_pr" = "0" ] && echo "STALL: issue #$n is status:in-review but has no open feat/$n-* or fix/$n-* PR"
done < <(jq -r '.[] | select(.l | index("status:in-review") != null) | .n' "$SCRATCH/open-issues.json")
echo
echo "Report complete. Raw data in \`$SCRATCH\`."
