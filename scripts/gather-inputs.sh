#!/usr/bin/env bash
# gather-inputs.sh <mode>: writes inputs/* for the harness. Runs with the App token (GH_TOKEN).
# Modes: json (issue/issues/pr/prs/factory/run json), phases (resolve phases, emit to GITHUB_OUTPUT),
#        context (copy profile.context_files from the target default branch), verify (deploy.json + smoke.json),
#        security (security.json: code scanning, Dependabot and secret scanning alerts; security-triage only).
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

# sha12 <text>: first 12 hex chars of sha256 (the marker hash, same as apply-proposal.sh).
sha12() {
  if command -v sha256sum >/dev/null 2>&1; then printf '%s' "$1" | sha256sum | cut -c1-12
  else printf '%s' "$1" | shasum -a 256 | cut -c1-12; fi
}

# fetch_feed <name> <api-path> <dir>: writes <dir>/<name>.data (JSON array, [] on failure) and <dir>/<name>.feed
# ({available, status, message}). Never fails: a non-200 is recorded. The success body is never printed; the
# error message comes from the API error body or gh's stderr line, never from alert data.
fetch_feed() {
  local name=$1 path=$2 dir=$3 rc=0 status msg
  gh api --paginate "$path" >"$dir/$name.raw" 2>"$dir/$name.err" || rc=$?
  if [ "$rc" -eq 0 ] && jq -s 'add // []' "$dir/$name.raw" >"$dir/$name.data" 2>/dev/null \
      && jq -e 'type == "array"' "$dir/$name.data" >/dev/null 2>&1; then
    jq -n '{available: true, status: 200, message: null}' >"$dir/$name.feed"
  else
    status="$(grep -Eo '\(HTTP [0-9]+\)' "$dir/$name.err" | tail -n1 | grep -Eo '[0-9]+' || true)"
    msg="$(jq -r 'objects | .message // empty' "$dir/$name.raw" 2>/dev/null | head -n1 || true)"
    [ -n "$msg" ] || msg="$(sed -E 's/^gh: //; s/ \(HTTP [0-9]+\)$//' "$dir/$name.err" | head -n1)"
    echo '[]' >"$dir/$name.data"
    jq -n --arg s "$status" --arg m "$msg" \
      '{available: false, status: (if $s == "" then null else ($s | tonumber) end), message: (if $m == "" then null else $m[0:300] end)}' \
      >"$dir/$name.feed"
    warn "security feed $name unavailable: ${status:-no status} ${msg:0:200}"
  fi
  rm -f "$dir/$name.raw" "$dir/$name.err"
}

mode_security() {
  local tmp; tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  local sev='def rank: {"critical": 4, "high": 3, "medium": 2, "low": 1}[. // ""] // 0;'

  fetch_feed code_scanning "repos/$TARGET_REPO/code-scanning/alerts?state=open&ref=refs/heads/$DEFAULT_BRANCH&per_page=100" "$tmp"
  fetch_feed dependabot "repos/$TARGET_REPO/dependabot/alerts?state=open&per_page=100" "$tmp"
  fetch_feed secret_scanning "repos/$TARGET_REPO/secret-scanning/alerts?state=open&hide_secret=true&per_page=100" "$tmp"

  # Open PRs by Dependabot (best effort: open_pr stays null when the list cannot be read).
  { gh pr list --repo "$TARGET_REPO" --state open --limit 100 --author app/dependabot --json number,headRefName,title 2>/dev/null \
      | jq '[.[] | {number, headRefName, title}]'; } >"$tmp/prs.json" || echo '[]' >"$tmp/prs.json"
  [ -s "$tmp/prs.json" ] || echo '[]' >"$tmp/prs.json"

  # Full (uncapped) lists; the caps apply only to what the agent sees.
  jq --arg ref "refs/heads/$DEFAULT_BRANCH" '
    [.[] | select(.most_recent_instance.ref == $ref)
     | (.most_recent_instance.location.path // "") as $path
     | {number, signature: "code_scanning:\(.rule.id):\($path)", rule_id: .rule.id, severity: .rule.severity,
        security_severity: .rule.security_severity_level, description: ((.rule.description // "") | .[0:500]),
        path: $path, start_line: .most_recent_instance.location.start_line,
        message: ((.most_recent_instance.message.text // "") | .[0:500]), html_url, created_at}]
    | sort_by(.created_at) | reverse' "$tmp/code_scanning.data" >"$tmp/cs.all"

  jq --slurpfile prs "$tmp/prs.json" "$sev"'
    [.[] | {number, package: .dependency.package.name, ecosystem: .dependency.package.ecosystem,
            manifest_path: .dependency.manifest_path, relationship: (.dependency.relationship // "unknown"),
            scope: .dependency.scope, ghsa_id: .security_advisory.ghsa_id, cve_id: .security_advisory.cve_id,
            severity: .security_advisory.severity, summary: ((.security_advisory.summary // "") | .[0:300]),
            vulnerable_range: .security_vulnerability.vulnerable_version_range,
            first_patched: .security_vulnerability.first_patched_version.identifier, html_url, created_at}]
    | group_by(.ecosystem + ":" + .package)
    | map(sort_by(.number) as $a | ($a[0].package) as $pkg | (($pkg | ltrimstr("@")) as $needle
        | ([$a[].relationship] | unique) as $rels
        | {signature: "dependabot:\($a[0].ecosystem):\($pkg)", package: $pkg, ecosystem: $a[0].ecosystem,
           manifest_path: $a[0].manifest_path,
           relationship: (if $rels == ["direct"] then "direct" else (($rels - ["direct"])[0]) end),
           scope: $a[0].scope, max_severity: ($a | max_by(.severity | rank) | .severity),
           open_pr: ([$prs[0][] | (.headRefName | split("/" + $needle + "-")) as $parts
                      | select(($parts | length) > 1 and ($parts[1] | test("^[0-9]"))) | .number] | first // null),
           alerts: [$a[] | {number, ghsa_id, cve_id, severity, summary, vulnerable_range, first_patched, html_url, created_at}]}))
    | sort_by([(.max_severity | rank) * -1, .signature])' "$tmp/dependabot.data" >"$tmp/dep.all"

  jq '[.[] | {secret_type, display_name: .secret_type_display_name}]
      | group_by(.secret_type)
      | map({signature: "secret_scanning:\(.[0].secret_type)", secret_type: .[0].secret_type,
             display_name: .[0].display_name, count: length})
      | {count: (map(.count) | add // 0), types: .}' "$tmp/secret_scanning.data" >"$tmp/ss.all"

  # tracked_issues: signature = the current alert signature whose hash matches, else null. When a feed is
  # unavailable its alerts are missing, so an unmatched hash proves nothing: those issues are left out.
  local any_unavailable=false
  if jq -s -e 'any(.[]; .available | not)' "$tmp/code_scanning.feed" "$tmp/dependabot.feed" "$tmp/secret_scanning.feed" >/dev/null; then
    any_unavailable=true
  fi
  { jq -r '.[].signature' "$tmp/cs.all"; jq -r '.[].signature' "$tmp/dep.all"; jq -r '.types[].signature' "$tmp/ss.all"; } \
    | sort -u | while IFS= read -r s; do
        [ -n "$s" ] && printf '%s\t%s\n' "$(sha12 "$s")" "$s"
      done \
    | jq -Rn '[inputs | split("\t") | {(.[0]): .[1]}] | add // {}' >"$tmp/hashmap.json"
  gh api --paginate "repos/$TARGET_REPO/issues?state=open&per_page=100" | jq -s 'add // []' \
    | jq --slurpfile map "$tmp/hashmap.json" --argjson gap "$any_unavailable" '
        [.[] | select(.pull_request == null) | select((.body // "") | contains("<!-- factory:security:"))
         | ((.body | capture("<!-- factory:security:(?<h>[0-9a-f]{12}) -->") | .h) // null) as $h
         | select($h != null)
         | {number, title, labels: [.labels[].name], marker_hash: $h, signature: ($map[0][$h] // null)}
         | select(($gap | not) or (.signature != null))]' >"$tmp/tracked.json"
  if [ "$any_unavailable" = true ]; then warn "security: a feed is unavailable; tracked issues with no matching alert are not reported as resolved"; fi

  jq -n --slurpfile cs_feed "$tmp/code_scanning.feed" --slurpfile dep_feed "$tmp/dependabot.feed" \
        --slurpfile ss_feed "$tmp/secret_scanning.feed" --slurpfile cs "$tmp/cs.all" --slurpfile dep "$tmp/dep.all" \
        --slurpfile ss "$tmp/ss.all" --slurpfile prs "$tmp/prs.json" --slurpfile tracked "$tmp/tracked.json" \
    '{feeds: {code_scanning: $cs_feed[0], dependabot: $dep_feed[0], secret_scanning: $ss_feed[0]},
      code_scanning: $cs[0][0:50], dependabot: $dep[0][0:50], secret_scanning: $ss[0],
      dependabot_prs: $prs[0], tracked_issues: $tracked[0]}' >"$INPUTS_DIR/security.json"
  log "security.json: $(jq -c '{cs: (.code_scanning | length), dep: (.dependabot | length), secrets: .secret_scanning.count, tracked: (.tracked_issues | length)}' "$INPUTS_DIR/security.json")"
}

case "$MODE" in
  json) mode_json ;;
  phases) mode_phases ;;
  context) mode_context ;;
  verify) mode_verify ;;
  security) mode_security ;;
  pr) fetch_pr_json "${PR:?PR required}" ;;
  *) die "unknown mode: $MODE" ;;
esac
