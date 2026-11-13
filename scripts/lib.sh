#!/usr/bin/env bash
# shellcheck shell=bash disable=SC2034,SC2016
# Shared helpers for the factory scripts. Sourced, never executed.
# Never echo tokens. Never enable `set -x` in scripts that source this file.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="${CONFIG:-$FACTORY_ROOT/factory.config.json}"
TARGET_REPO="${TARGET_REPO:-$(jq -r '"\(.target.owner)/\(.target.repo)"' "$CONFIG")}"
DEFAULT_BRANCH="$(jq -r '.target.default_branch' "$CONFIG")"
APP_SLUG="$(jq -r '.factory.app_slug' "$CONFIG")"
FACTORY_REF="$(jq -r '.factory.ref' "$CONFIG")"
INPUTS_DIR="${INPUTS_DIR:-inputs}"
OUT_DIR="${OUT_DIR:-out}"
WORK_DIR="${WORK_DIR:-work}"

PLAN_MARKER='<!-- factory:plan -->'
REVIEW_MARKER='<!-- factory:review -->'
VERIFY_MARKER='<!-- factory:verify -->'

# Labels that only a human (or a deterministic post-step) may set. Agent proposals can never add them.
HUMAN_ONLY_LABELS='["plan:approved","factory:accepted","ci-fix:attempted","autonomy:dark","autonomy:lit"]'

log() { printf '%s\n' "$*" >&2; }
warn() { printf '::warning::%s\n' "$*" >&2; }
die() { printf '::error::%s\n' "$*" >&2; exit 1; }

# jq helpers. normauthor maps gh author objects to the contract form (app/<slug> for bots).
JQ_NORM='def normauthor: if . == null then "ghost" else (.login // "ghost") as $l
  | if ($l | startswith("app/")) then $l
    elif (.is_bot == true or ($l | endswith("[bot]"))) then "app/" + ($l | sub("\\[bot\\]$"; ""))
    else $l end end;'
# noat: neutralise @mentions by inserting a zero-width space after the @.
JQ_NOAT='def noat: gsub("(?<pre>(?:^|[^A-Za-z0-9/]))@(?<u>[A-Za-z0-9_])"; "\(.pre)@​\(.u)");'

# sanitize: stdin text -> stdout text with @mentions neutralised.
sanitize() { jq -Rrs "$JQ_NOAT"' noat'; }

# filter_labels: stdin JSON array of label names -> stdout one per line, only labels in
# factory.config.json and not in HUMAN_ONLY_LABELS.
filter_labels() {
  jq -r --argjson deny "$HUMAN_ONLY_LABELS" --slurpfile cfg "$CONFIG" '
    ($cfg[0].labels | [.[][]]) as $ok
    | (. // []) | map(select(. as $l | ($ok | index($l)) and (($deny | index($l)) | not)))
    | unique | .[]'
}

csv() { paste -sd, - ; }

# transient_gh_error <stderr-file>: true when the failure looks transient (HTTP 5xx or a network error
# from gh or git), so a retry can succeed. A 4xx (validation, permissions, not found) is never transient.
transient_gh_error() {
  grep -qiE 'HTTP 5[0-9]{2}|connection (reset|refused|timed out)|could not resolve host|timed? ?out|unexpected EOF|early EOF|TLS handshake|remote end hung up|RPC failed|temporarily unavailable' "$1"
}

# gh_retry <cmd...>: run a GitHub write (gh or git_auth) and retry transient failures with exponential
# backoff: GH_RETRY_MAX attempts (default 4) waiting GH_RETRY_BASE * 2^(n-1) seconds (default 2: 2, 4, 8)
# plus up to a second of jitter. Non-transient failures return at once. Stdout passes through; the
# failing attempt's stderr is logged as a warning, and the last one is passed through on give-up.
gh_retry() {
  local max=${GH_RETRY_MAX:-4} base=${GH_RETRY_BASE:-2} attempt=1 err rc
  err="$(mktemp)"
  while :; do
    if "$@" 2>"$err"; then rm -f "$err"; return 0; else rc=$?; fi
    if [ "$attempt" -ge "$max" ] || ! transient_gh_error "$err"; then
      cat "$err" >&2; rm -f "$err"; return "$rc"
    fi
    warn "$1 ${2:-} attempt $attempt failed ($(head -c 160 "$err" | tr '\n' ' ')); retrying"
    sleep "$(awk -v b="$base" -v n="$attempt" 'BEGIN { srand(); printf "%.3f", b * (2 ^ (n - 1)) + rand() }')"
    attempt=$((attempt + 1))
  done
}

# edit_labels <issue|pr> <number> <add-csv> <remove-csv>
edit_labels() {
  local kind=$1 num=$2 add=${3:-} rm=${4:-}
  local args=()
  if [ -n "$add" ]; then args+=(--add-label "$add"); fi
  if [ -n "$rm" ]; then args+=(--remove-label "$rm"); fi
  if [ ${#args[@]} -eq 0 ]; then return 0; fi
  gh_retry gh "$kind" edit "$num" --repo "$TARGET_REPO" "${args[@]}" >/dev/null
}

# open_pr_for <branch>: stdout the number of the open PR whose head is <branch>, or empty.
open_pr_for() {
  gh pr list --repo "$TARGET_REPO" --head "$1" --state open --json number --jq '.[0].number // empty'
}

# create_pr <branch> <gh pr create args...>: open the PR and print its number. A transient failure
# (a 504 from the gateway) may still have created the PR, so every failure first looks for an open PR
# on the branch and uses it; otherwise transient errors are retried like gh_retry. On give-up the
# error names the branch, which is already pushed.
create_pr() {
  local branch=$1; shift
  local max=${GH_RETRY_MAX:-4} base=${GH_RETRY_BASE:-2} attempt=1 err url num
  err="$(mktemp)"
  while :; do
    if url="$(gh pr create "$@" 2>"$err")"; then rm -f "$err"; printf '%s' "${url##*/}"; return 0; fi
    num="$(open_pr_for "$branch" 2>/dev/null || true)"
    if [ -n "$num" ]; then
      warn "pr create failed but PR #$num is open for $branch; using it"
      rm -f "$err"; printf '%s' "$num"; return 0
    fi
    if [ "$attempt" -ge "$max" ] || ! transient_gh_error "$err"; then
      log "::error::could not open a PR for $branch after $attempt attempt(s); the branch is pushed, commits are on origin/$branch"
      cat "$err" >&2; rm -f "$err"; return 1
    fi
    warn "pr create attempt $attempt failed ($(head -c 160 "$err" | tr '\n' ' ')); retrying"
    sleep "$(awk -v b="$base" -v n="$attempt" 'BEGIN { srand(); printf "%.3f", b * (2 ^ (n - 1)) + rand() }')"
    attempt=$((attempt + 1))
  done
}

# git_auth <git args...>: run git with the App token injected through the environment (not argv, not on disk).
git_auth() {
  local b64
  b64="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
  GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" \
  GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${b64}" \
    git "$@"
}

# fetch_plan_comment <issue-number>: stdout body of the latest bot-authored plan comment, or empty.
fetch_plan_comment() {
  gh api --paginate "repos/$TARGET_REPO/issues/$1/comments" \
    | jq -s -r --arg m "$PLAN_MARKER" \
      '[.[][] | select((.body | contains($m)) and (.user.login | endswith("[bot]"))) | .body] | last // empty'
}

# fetch_pr_json <number>: stdout pr.json per CONTRACTS.md.
fetch_pr_json() {
  local n=$1 tmp raw linked
  tmp="$(mktemp -d)"
  # `gh pr view --json` does not expose authorAssociation; take it from the REST API.
  raw="$(gh pr view "$n" --repo "$TARGET_REPO" --json number,title,body,author,labels,headRefName,baseRefName,isDraft,mergeable,files,statusCheckRollup,reviews)"
  local assoc; assoc="$(gh api "repos/$TARGET_REPO/pulls/$n" --jq '.author_association // "NONE"')"
  { gh pr diff "$n" --repo "$TARGET_REPO" 2>/dev/null | head -c 200000 >"$tmp/diff"; } || true
  # shellcheck disable=SC2016
  gh api graphql -F owner="${TARGET_REPO%%/*}" -F name="${TARGET_REPO##*/}" -F number="$n" -f query='
    query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){
      reviewThreads(first:100){nodes{id isResolved path comments(first:1){nodes{body}}}}}}}' \
    --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | {id, isResolved, path, body: (.comments.nodes[0].body // "")}]' \
    >"$tmp/threads"
  linked="$(jq -r '(.body // "") | capture("(?i)(closes|fixes|resolves) #(?<n>[0-9]+)") | .n' <<<"$raw" 2>/dev/null || true)"
  { gh api --paginate "repos/$TARGET_REPO/pulls/$n/reviews" 2>/dev/null || echo '[]'; } \
    | jq -s --arg m "$REVIEW_MARKER" \
      '[.[][] | select((.body // "") | contains($m))] | sort_by(.submitted_at) | last
       | if . == null then null else {body, commit_id, submitted_at} end' >"$tmp/factory_review"
  : >"$tmp/plan"
  if [ -n "$linked" ]; then fetch_plan_comment "$linked" >"$tmp/plan" 2>/dev/null || true; fi
  jq "$JQ_NORM"'
    {number, title, body: (.body // ""), author: (.author | normauthor), authorAssociation: $assoc,
     labels: [.labels[].name], headRefName, baseRefName, isDraft, mergeable,
     files: [.files[] | {path, additions, deletions}],
     diff: $diff,
     checks: [.statusCheckRollup[]? | {name: (.name // .context), status: ((.status // "COMPLETED") | ascii_downcase),
                                      conclusion: ((.conclusion // .state // "") | ascii_downcase)}],
     reviews: [.reviews[]? | {author: (.author | normauthor), state}],
     reviewThreads: $threads[0],
     plan_comment: (if $plan == "" then null else $plan end),
     factory_review: $freview[0]}' \
    --slurpfile freview "$tmp/factory_review" --arg assoc "$assoc" --rawfile diff "$tmp/diff" --slurpfile threads "$tmp/threads" --rawfile plan "$tmp/plan" \
    <<<"$raw"
  rm -rf "$tmp"
}
