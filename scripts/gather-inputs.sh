#!/usr/bin/env bash
# gather-inputs.sh <mode>: writes inputs/* for the harness. Runs with the App token (GH_TOKEN).
# Modes: json (issue/issues/pr/prs/factory/run json), phases (resolve phases, emit to GITHUB_OUTPUT),
#        context (copy profile.context_files from the target default branch), verify (deploy.json + smoke.json).
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MODE="${1:-json}"
AGENT="${AGENT:?AGENT required}"
RUN_ID="${RUN_ID:-local}"
PHASE="${PHASE:-}"
ISSUE="${ISSUE:-}"
PR="${PR:-}"
DRY_RUN="${DRY_RUN:-false}"
TRIGGER="${TRIGGER:-${GITHUB_EVENT_NAME:-workflow_dispatch}}"
mkdir -p "$INPUTS_DIR" "$OUT_DIR"

emit_output() { if [ -n "${GITHUB_OUTPUT:-}" ]; then printf '%s=%s\n' "$1" "$2" >>"$GITHUB_OUTPUT"; fi; }

mode_json() {
  local main_sha commit_sha parsed raw
  main_sha="$(gh api "repos/$TARGET_REPO/commits/$DEFAULT_BRANCH" --jq .sha)"
  commit_sha="$main_sha"

  if [ -n "$ISSUE" ]; then
    # REST, not `gh issue view --json`: the CLI does not expose authorAssociation for issues.
    jq -n --slurpfile issue <(gh api "repos/$TARGET_REPO/issues/$ISSUE") \
          --slurpfile comments <(gh api --paginate "repos/$TARGET_REPO/issues/$ISSUE/comments" | jq -s 'add // []') \
      '$issue[0] | {number, title, body: (.body // ""), state: (.state | ascii_upcase), createdAt: .created_at,
         author: {login: (.user.login // "ghost")}, authorAssociation: .author_association,
         labels: [.labels[].name],
         comments: [$comments[0][] | {author: (.user.login // "ghost"), authorAssociation: .author_association,
                                       body: (.body // ""), createdAt: .created_at}]}' \
      >"$INPUTS_DIR/issue.json"
  fi

  # The issues endpoint also returns PRs; drop those. Cap at 200 like the old --limit.
  gh api --paginate "repos/$TARGET_REPO/issues?state=open&per_page=100" | jq -s 'add // []' \
    | jq '[.[] | select(.pull_request == null)
           | {number, title, labels: [.labels[].name], state: (.state | ascii_upcase), authorAssociation: .author_association,
              body_excerpt: ((.body // "") | .[0:500])}] | .[0:200]' \
    >"$INPUTS_DIR/issues.json"

  gh pr list --repo "$TARGET_REPO" --state open --limit 100 \
    --json number,title,author,labels,headRefName,createdAt \
    | jq "$JQ_NORM"'[.[] | {number, title, author: (.author | normauthor), labels: [.labels[].name], headRefName, createdAt}]' \
    >"$INPUTS_DIR/prs.json"

  if [ -n "$PR" ]; then
    fetch_pr_json "$PR" >"$INPUTS_DIR/pr.json"
    raw="$(gh pr view "$PR" --repo "$TARGET_REPO" --json mergeCommit,headRefOid)"
    commit_sha="$(jq -r '.mergeCommit.oid // .headRefOid' <<<"$raw")"
  fi

  # The profile is read from the default branch only; the harness owns every default.
  parsed="$(gh api -H 'Accept: application/vnd.github.raw' "repos/$TARGET_REPO/contents/factory.yml?ref=$DEFAULT_BRANCH" 2>/dev/null)" \
    || die "factory.yml not found on $DEFAULT_BRANCH of $TARGET_REPO; the target needs a profile (see templates/target/factory.yml)"
  yq -o=json '.' <<<"$parsed" >"$INPUTS_DIR/factory.json" || die "factory.yml is not valid YAML"
  (cd "$FACTORY_ROOT" && uv run --frozen python -m factory.schemas validate-profile --file "$INPUTS_DIR/factory.json" --write) \
    || die "factory.yml failed profile validation"

  jq -n --arg run_id "$RUN_ID" --arg agent "$AGENT" --arg phase "$PHASE" --arg dry "$DRY_RUN" \
    --arg trigger "$TRIGGER" --arg repo "$TARGET_REPO" --arg issue "$ISSUE" --arg pr "$PR" --arg sha "$commit_sha" \
    '{run_id: $run_id, agent: $agent, phase: $phase, dry_run: ($dry == "true"), trigger: $trigger, target_repo: $repo,
      issue_number: (if $issue == "" then null else ($issue | tonumber) end),
      pr_number: (if $pr == "" then null else ($pr | tonumber) end), commit_sha: $sha}' \
    >"$INPUTS_DIR/run.json"
  emit_output commit_sha "$commit_sha"
}

mode_phases() {
  local allowed phases plan
  allowed="$(jq -r --arg a "$AGENT" '.agents[$a].phases // empty | join(" ")' "$CONFIG")"
  [ -n "$allowed" ] || die "unknown agent: $AGENT"
  if [ -n "$PHASE" ]; then
    case " $allowed " in *" $PHASE "*) phases="$PHASE" ;; *) die "phase '$PHASE' not valid for $AGENT (allowed: $allowed)" ;; esac
  else
    phases="$allowed"
    case "$AGENT" in
      builder|bug-fixer)
        if [ -n "$ISSUE" ] && jq -e 'any(.labels[]; . == "plan:approved")' "$INPUTS_DIR/issue.json" >/dev/null; then
          plan="$(fetch_plan_comment "$ISSUE" || true)"
          if [ -n "$plan" ]; then
            printf '%s\n' "$plan" | grep -v -e '^<!-- factory:plan' >"$OUT_DIR/plan.md" || true
            phases="implement"
            log "plan:approved with existing plan comment: running implement only"
          fi
        fi ;;
    esac
  fi
  if [ "$phases" = "implement" ] && [ -n "$PHASE" ] && [ ! -s "$OUT_DIR/plan.md" ] && [ -n "$ISSUE" ]; then
    plan="$(fetch_plan_comment "$ISSUE" || true)"
    printf '%s\n' "$plan" | grep -v -e '^<!-- factory:plan' >"$OUT_DIR/plan.md" || true
  fi
  log "phases: $phases"
  emit_output phases "$phases"
}

mode_context() {
  local ref="origin/$DEFAULT_BRANCH" pat f tree
  mkdir -p "$INPUTS_DIR/context"
  rm -f "$INPUTS_DIR/context/"*
  # Always from the default branch, never from a PR head. Patterns are globs from profile.context_files.
  tree="$(git -C "$WORK_DIR" ls-tree -r --name-only "$ref")"
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    while IFS= read -r f; do
      # shellcheck disable=SC2254
      case "$f" in $pat) git -C "$WORK_DIR" show "$ref:$f" >"$INPUTS_DIR/context/$(basename "$f")" ;; *) ;; esac
    done <<<"$tree"
  done < <(jq -r '.context_files[]' "$INPUTS_DIR/factory.json")
}

mode_verify() {
  local expected
  expected="$(jq -r '.commit_sha' "$INPUTS_DIR/run.json")"
  if [ "$(jq -r '.smoke.version == null' "$INPUTS_DIR/factory.json")" = "true" ]; then
    log "profile has no smoke.version; skipping deploy wait"
    jq -n '{skipped: true}' >"$INPUTS_DIR/deploy.json"
  else
    EXPECTED_SHA="$expected" "$SCRIPT_DIR/wait-for-deploy.sh"
  fi

  rm -f "$FACTORY_ROOT/smoke/playwright-report.json"
  if [ -z "${TARGET_BASE_URL:-}" ]; then
    warn "TARGET_BASE_URL unset; skipping smoke"
    jq -n '{passed: false, tests: [{name: "smoke", status: "skipped", error: "TARGET_BASE_URL not set"}]}' >"$INPUTS_DIR/smoke.json"
    return 0
  fi
  (cd "$FACTORY_ROOT/smoke" && FACTORY_PROFILE="$(cd "$FACTORY_ROOT" && realpath "$INPUTS_DIR/factory.json")" \
    SMOKE_AUTH_TOKEN="${SMOKE_AUTH_TOKEN:-}" npx playwright test) || warn "smoke tests reported failures"
  if [ -s "$FACTORY_ROOT/smoke/playwright-report.json" ]; then
    node "$FACTORY_ROOT/smoke/to-smoke-json.mjs" "$FACTORY_ROOT/smoke/playwright-report.json" >"$INPUTS_DIR/smoke.json"
  else
    jq -n '{passed: false, tests: [{name: "smoke", status: "failed", error: "no playwright report produced"}]}' >"$INPUTS_DIR/smoke.json"
  fi
}

case "$MODE" in
  json) mode_json ;;
  phases) mode_phases ;;
  context) mode_context ;;
  verify) mode_verify ;;
  pr) fetch_pr_json "${PR:?PR required}" ;;
  *) die "unknown mode: $MODE" ;;
esac
