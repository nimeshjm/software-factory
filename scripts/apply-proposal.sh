#!/usr/bin/env bash
# apply-proposal.sh <agent> [phase|--auto]: the only code that mutates GitHub. Runs with the App token (GH_TOKEN).
# --auto applies every phase listed in out/phases_ok.txt (research is skipped: it has no side effects).
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

AGENT="${1:?usage: apply-proposal.sh <agent> [phase|--auto]}"
MODE="${2:---auto}"
: "${GH_TOKEN:?GH_TOKEN (App token) required}"
ISSUE="${ISSUE:-}"
PR="${PR:-}"
RUN_ID="${RUN_ID:-local}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FINDINGS_MARKER='<!-- factory:findings -->'

uvpy() { uv run --frozen python "$@"; }

proposal_path() {
  if [ -f "$OUT_DIR/phases/$1/proposal.json" ]; then printf '%s' "$OUT_DIR/phases/$1/proposal.json"; else printf '%s' "$OUT_DIR/proposal.json"; fi
}

# validate_and_guard <phase> <file>
validate_and_guard() {
  local phase=$1 f=$2 rc=0
  [ -s "$f" ] || die "missing proposal for $AGENT/$phase"
  uvpy -m factory.schemas validate --agent "$AGENT" --phase "$phase" --file "$f" || die "proposal failed schema validation"
  uvpy -m factory.guards.output_guard --file "$f" --profile "$INPUTS_DIR/factory.json" >"$TMP/guard.json" || rc=$?
  if [ "$rc" -ne 0 ]; then
    jq -r '.findings[]?' "$TMP/guard.json" >&2 || true
    die "output_guard blocked the proposal (exit $rc)"
  fi
}

# guard_diff <git diff args...>: diff in work/ must be clean and must not touch .github/ or factory.yml
guard_diff() {
  local rc=0
  git -C "$WORK_DIR" diff "$@" >"$TMP/work.diff"
  git -C "$WORK_DIR" diff --name-only "$@" >"$TMP/work.files"
  if grep -qE '^(\.github/|factory\.yml$)' "$TMP/work.files"; then die "diff touches .github/** or factory.yml; aborting"; fi
  uvpy -m factory.guards.output_guard --file "$TMP/work.diff" --profile "$INPUTS_DIR/factory.json" >"$TMP/guard-diff.json" || rc=$?
  if [ "$rc" -ne 0 ]; then
    jq -r '.findings[]?' "$TMP/guard-diff.json" >&2 || true
    die "output_guard blocked the diff (exit $rc)"
  fi
}

# upsert_comment <issue|pr number> <marker> <bodyfile>   (body must start with marker)
upsert_comment() {
  local num=$1 marker=$2 bodyfile=$3 id
  id="$(gh api --paginate "repos/$TARGET_REPO/issues/$num/comments" \
    | jq -s -r --arg m "$marker" '[.[][] | select((.body | startswith($m)) and (.user.login | endswith("[bot]")))] | last | (.id // empty)')"
  if [ -n "$id" ]; then
    gh api -X PATCH "repos/$TARGET_REPO/issues/comments/$id" -F "body=@$bodyfile" >/dev/null
  else
    gh api -X POST "repos/$TARGET_REPO/issues/$num/comments" -F "body=@$bodyfile" >/dev/null
  fi
}

# create_issue <title> <body-file> <labels-csv> [marker]: dedupes against open issues; prints number or empty
create_issue() {
  local title=$1 bodyfile=$2 labels=$3 marker=${4:-} existing url
  existing="$(gh issue list --repo "$TARGET_REPO" --state open --limit 200 --json number,title,body \
    | jq -r --arg t "$title" --arg m "$marker" \
      '[.[] | select((.title | ascii_downcase) == ($t | ascii_downcase) or ($m != "" and ((.body // "") | contains($m)))) | .number] | first // empty')"
  if [ -n "$existing" ]; then
    log "dedupe: issue #$existing already covers '$title'"
    printf '%s' "$existing"
    return 0
  fi
  url="$(gh issue create --repo "$TARGET_REPO" --title "$title" --body-file "$bodyfile" ${labels:+--label "$labels"})"
  printf '%s' "${url##*/}"
}

author_is_trusted() { # <association> <labels-json>
  case "$1" in OWNER|COLLABORATOR|MEMBER) return 0 ;; esac
  jq -e 'index("factory:accepted")' <<<"$2" >/dev/null
}

# ---------------------------------------------------------------- triage
apply_triage() {
  local f; f="$(proposal_path triage)"; validate_and_guard triage "$f"
  local count i num add rm comment assoc labels labels_json
  count="$(jq '.issues | length' "$f")"
  for ((i = 0; i < count; i++)); do
    num="$(jq -r ".issues[$i].number" "$f")"
    labels_json="$(jq -c --argjson n "$num" '[.[] | select(.number == $n)] | first // empty' "$INPUTS_DIR/issues.json")"
    if [ -z "$labels_json" ]; then warn "triage: #$num not in open issues; skipped"; continue; fi
    assoc="$(jq -r '.authorAssociation' <<<"$labels_json")"
    labels="$(jq -c '.labels' <<<"$labels_json")"
    if author_is_trusted "$assoc" "$labels"; then
      add="$(jq -c ".issues[$i].labels_add" "$f" | filter_labels | csv)"
    else
      add="needs-human" # strangers: needs-human only
    fi
    rm="$(jq -c ".issues[$i].labels_remove" "$f" | jq -r '.[]' | grep -vxE 'needs-human|plan:approved|factory:accepted|autonomy:.*' | csv || true)"
    edit_labels issue "$num" "$add" "$rm"
    comment="$(jq -r ".issues[$i].comment // empty" "$f")"
    if [ -n "$comment" ]; then
      printf '<!-- factory:triage -->\n%s\n' "$comment" | sanitize >"$TMP/c.md"
      if ! gh api --paginate "repos/$TARGET_REPO/issues/$num/comments" | jq -s -e --rawfile b "$TMP/c.md" 'any(.[][]; .body == ($b | rtrimstr("\n")))' >/dev/null; then
        gh issue comment "$num" --repo "$TARGET_REPO" --body-file "$TMP/c.md" >/dev/null
      fi
    fi
  done

  local nnew created=() title body parent lbls k out src src_json
  nnew="$(jq '.new_issues | length' "$f")"
  if [ "$nnew" -gt 10 ]; then warn "triage: capping new issues at 10"; nnew=10; fi
  for ((i = 0; i < nnew; i++)); do
    title="$(jq -r ".new_issues[$i].title" "$f")"
    body="$(jq -r ".new_issues[$i].body" "$f")"
    parent="$(jq -r ".new_issues[$i].parent // empty" "$f")"
    for ((k = 0; k < ${#created[@]}; k++)); do body="${body//\{child:$((k + 1))\}/#${created[$k]}}"; done
    body="$(sed -E 's/\{child:[0-9]+\}/(unresolved)/g' <<<"$body")"
    if [ -n "$parent" ]; then body="$body"$'\n\n'"Part of #$parent"; fi
    printf '%s\n\n<!-- factory:run %s -->\n' "$body" "$RUN_ID" | sanitize >"$TMP/b.md"
    lbls="$(jq -c ".new_issues[$i].labels" "$f" | filter_labels | csv)"
    # The App bot is a CONTRIBUTOR, so children need factory:accepted to pass the author gate; inherit it from a trusted parent (or the triaged issue).
    src="${parent:-$ISSUE}"
    src_json=""
    if [ -n "$src" ]; then src_json="$(jq -c --argjson n "$src" '[.[] | select(.number == $n)] | first // empty' "$INPUTS_DIR/issues.json")"; fi
    if [ -n "$src_json" ] && author_is_trusted "$(jq -r '.authorAssociation' <<<"$src_json")" "$(jq -c '.labels' <<<"$src_json")"; then
      lbls="${lbls:+$lbls,}factory:accepted"
    fi
    out="$(create_issue "$title" "$TMP/b.md" "$lbls")"
    created+=("${out:-0}")
    if [ -n "$parent" ] && [ -n "$out" ]; then edit_labels issue "$parent" epic ""; fi
  done
  log "triage applied: $(jq -r '.summary' "$f" | head -c 300)"
}

# ---------------------------------------------------------------- plan (builder, bug-fixer)
apply_plan() {
  local f; f="$(proposal_path plan)"; validate_and_guard plan "$f"
  [ -n "$ISSUE" ] || die "plan requires ISSUE"
  { printf '%s\n' "$PLAN_MARKER"
    jq -r '"<!-- factory:plan-files " + ({files: .files} | tojson) + " -->"' "$f"
    jq -r '.plan_md' "$f"
    printf '\n_Factory run %s_\n' "$RUN_ID"
  } | sanitize >"$TMP/plan.md"
  # sanitize leaves the marker intact (no @); ensure the file starts with it
  upsert_comment "$ISSUE" "$PLAN_MARKER" "$TMP/plan.md"

  local needs_human labels risk_high approved
  needs_human="$(jq -r '.needs_human' "$f")"
  labels="$(gh issue view "$ISSUE" --repo "$TARGET_REPO" --json labels --jq '[.labels[].name]')"
  risk_high="$(jq -r 'any(.[]; . == "risk:high")' <<<"$labels")"
  approved="$(jq -r 'any(.[]; . == "plan:approved")' <<<"$labels")"
  if [ "$needs_human" = "true" ]; then
    edit_labels issue "$ISSUE" "status:plan-review,needs-human" "status:backlog,status:in-progress"
  elif [ "$risk_high" = "true" ] && [ "$approved" != "true" ]; then
    edit_labels issue "$ISSUE" "status:plan-review" "status:backlog,status:in-progress"
  else
    edit_labels issue "$ISSUE" "status:in-progress" "status:backlog,status:plan-review"
  fi
}

# ---------------------------------------------------------------- implement
apply_implement() {
  local f; f="$(proposal_path implement)"; validate_and_guard implement "$f"
  [ -n "$ISSUE" ] || die "implement requires ISSUE"
  # Spike: no files but findings -> deliver an issue comment and close; no branch, push or PR.
  if jq -e '(.files | length) == 0 and ((.findings_md // "") | type == "string" and length > 0)' "$f" >/dev/null; then
    [ "$AGENT" = builder ] || die "spike findings are builder-only (agent: $AGENT)"
    jq -e '(.web.allowed_hosts // []) | length > 0' "$INPUTS_DIR/factory.json" >/dev/null \
      || die "spike delivery disabled: profile web.allowed_hosts is empty"
    { printf '%s\n' "$FINDINGS_MARKER"
      jq -r '.findings_md' "$f"
      printf '\n---\n_Factory run %s_\n' "$RUN_ID"
    } | sanitize >"$TMP/findings.md"
    # sanitize leaves the marker intact (no @), so the comment still starts with it
    upsert_comment "$ISSUE" "$FINDINGS_MARKER" "$TMP/findings.md"
    edit_labels issue "$ISSUE" "status:done" "status:in-progress,status:plan-review,status:backlog"
    if [ "$(gh issue view "$ISSUE" --repo "$TARGET_REPO" --json state --jq .state)" != CLOSED ]; then
      gh issue close "$ISSUE" --repo "$TARGET_REPO" --reason completed >/dev/null
    fi
    log "spike findings posted on #$ISSUE; issue closed (no PR)"
    return 0
  fi
  local branch title ok prefix cur ahead existing pr_url
  branch="$(jq -r '.branch' "$f")"
  if ! [[ "$branch" =~ ^(feat|fix)/[0-9]+-[a-z0-9-]+$ ]]; then die "branch '$branch' does not match feat|fix/<N>-<slug>"; fi
  prefix="${branch%%/*}"
  if { [ "$AGENT" = builder ] && [ "$prefix" != feat ]; } || { [ "$AGENT" = bug-fixer ] && [ "$prefix" != fix ]; }; then
    die "branch prefix '$prefix' not allowed for $AGENT"
  fi
  if [[ "${branch#*/}" != "$ISSUE-"* ]]; then die "branch number must equal issue #$ISSUE"; fi
  cur="$(git -C "$WORK_DIR" rev-parse --abbrev-ref HEAD)"
  if [ "$cur" != "$branch" ]; then
    # The pipeline creates the branch (inputs/branch.txt) before implement; the proposal's name is advisory.
    if [ -s "$INPUTS_DIR/branch.txt" ] && [ "$(cat "$INPUTS_DIR/branch.txt")" = "$cur" ]; then
      warn "proposal branch '$branch' differs from the pipeline branch '$cur'; using '$cur'"
      branch="$cur"
    else
      die "work/ is on '$cur', proposal says '$branch'"
    fi
  fi
  ahead="$(git -C "$WORK_DIR" rev-list --count "origin/$DEFAULT_BRANCH..HEAD")"
  [ "$ahead" -gt 0 ] || die "no commits ahead of $DEFAULT_BRANCH"
  if [ -n "$(git -C "$WORK_DIR" status --porcelain)" ]; then warn "uncommitted changes in work/ are not pushed"; fi
  guard_diff "origin/$DEFAULT_BRANCH...HEAD"

  existing="$(gh pr list --repo "$TARGET_REPO" --head "$branch" --state open --json number --jq '.[0].number // empty')"
  if [ -n "$existing" ]; then
    log "PR #$existing already open for $branch"
    printf '%s' "$existing" >"$OUT_DIR/created-pr.txt"
    return 0
  fi
  git_auth -C "$WORK_DIR" push -u origin "$branch"

  title="$(jq -r '.title' "$f")"
  { jq -r '.body' "$f"
    if ! jq -r '.body' "$f" | grep -qiE "closes #$ISSUE([^0-9]|$)"; then printf '\n\nCloses #%s\n' "$ISSUE"; fi
    printf '\n---\n_Factory run %s_\n' "$RUN_ID"
  } | sanitize >"$TMP/pr.md"
  ok="$(jq -r '.checks_passed' "$f")"
  local args=(--repo "$TARGET_REPO" --base "$DEFAULT_BRANCH" --head "$branch" --title "$title" --body-file "$TMP/pr.md")
  if [ "$ok" != "true" ]; then args+=(--draft); fi
  pr_url="$(gh pr create "${args[@]}")"
  printf '%s' "${pr_url##*/}" >"$OUT_DIR/created-pr.txt"
  edit_labels issue "$ISSUE" "status:in-review" "status:in-progress,status:plan-review,status:backlog"
  log "opened $pr_url"
}

# ---------------------------------------------------------------- reviewer
post_review() { # <proposal> <head_sha>
  local f=$1 sha=$2 event body_attempt
  event="$(jq -r '{approve: "COMMENT", comment: "COMMENT", needs_human: "COMMENT", request_changes: "REQUEST_CHANGES"}[.verdict]' "$f")"
  jq -r --arg marker "$REVIEW_MARKER" '
    $marker + "\n" + .summary_md
    + (if (.summary_md | test("questions for the human"; "i")) then "" else
        (if (.agent_checks | length) > 0 then "\n\n### Agent checks\n\n| Check | Status | Detail |\n|---|---|---|\n"
           + ([.agent_checks[] | "| \(.name) | \(.status) | \(.detail | gsub("\n"; " ") | gsub("\\|"; "/")) |"] | join("\n")) else "" end)
        + (if (.questions_for_human | length) > 0 then "\n\n### Questions for the human\n\n" + ([.questions_for_human[] | "- " + .] | join("\n")) else "" end)
      end)' "$f" | sanitize >"$TMP/review.md"
  jq -r '.inline_comments[] | "- `\(.path):\(.line)` \(.body)"' "$f" | sanitize >"$TMP/inline.md"
  # Attempt 1: event + inline comments. 2: event, inline in body. 3: COMMENT, inline in body.
  jq -n --rawfile b "$TMP/review.md" --arg e "$event" --arg sha "$sha" --slurpfile p "$f" "$JQ_NOAT"'
    {commit_id: $sha, event: $e, body: $b,
     comments: [$p[0].inline_comments[] | {path, line, side: "RIGHT", body: (.body | noat)}]}' >"$TMP/rv1.json"
  { cat "$TMP/review.md"; if [ -s "$TMP/inline.md" ]; then printf '\n\n### Inline notes\n\n'; cat "$TMP/inline.md"; fi; } >"$TMP/review2.md"
  jq -n --rawfile b "$TMP/review2.md" --arg e "$event" --arg sha "$sha" '{commit_id: $sha, event: $e, body: $b}' >"$TMP/rv2.json"
  jq -n --rawfile b "$TMP/review2.md" --arg sha "$sha" '{commit_id: $sha, event: "COMMENT", body: $b}' >"$TMP/rv3.json"
  for body_attempt in rv1 rv2 rv3; do
    if gh api -X POST "repos/$TARGET_REPO/pulls/$PR/reviews" --input "$TMP/$body_attempt.json" >/dev/null 2>"$TMP/rv.err"; then return 0; fi
    warn "review attempt $body_attempt failed: $(head -c 200 "$TMP/rv.err")"
  done
  die "could not post review"
}

apply_review() {
  local f; f="$(proposal_path review)"; validate_and_guard review "$f"
  [ -n "$PR" ] || die "review requires PR"
  local head_sha existing verdict fresh author autonomy plan_comment trigger decision merge linked
  head_sha="$(gh pr view "$PR" --repo "$TARGET_REPO" --json headRefOid --jq .headRefOid)"
  existing="$(gh api --paginate "repos/$TARGET_REPO/pulls/$PR/reviews" \
    | jq -s -r --arg m "$REVIEW_MARKER" --arg s "$head_sha" '[.[][] | select(((.body // "") | contains($m)) and .commit_id == $s)] | length')"
  if [ "$existing" = "0" ]; then post_review "$f" "$head_sha"; else log "review for $head_sha already posted"; fi
  verdict="$(jq -r '.verdict' "$f")"
  if [ "$verdict" = "needs_human" ]; then edit_labels pr "$PR" "needs-human" ""; fi

  fresh="$TMP/pr.fresh.json"
  fetch_pr_json "$PR" >"$fresh"
  autonomy="$(uvpy -m factory.policy classify-autonomy --pr "$fresh" --factory "$INPUTS_DIR/factory.json" | tr -d '[:space:]')"
  case "$autonomy" in dark|lit) ;; *) die "classify-autonomy returned '$autonomy'" ;; esac
  if [ "$autonomy" = dark ]; then edit_labels pr "$PR" "autonomy:dark" "autonomy:lit"; else edit_labels pr "$PR" "autonomy:lit" "autonomy:dark"; fi

  author="$(jq -r '.author' "$fresh")"
  trigger=webhook
  if [ "$author" = "app/dependabot" ]; then trigger=cron; fi
  local pf_args=()
  plan_comment="$(jq -r '.plan_comment // empty' "$fresh")"
  if [ -n "$plan_comment" ]; then
    printf '%s\n' "$plan_comment" | sed -n 's/.*<!-- factory:plan-files \(.*\) -->.*/\1/p' | head -n1 | jq -c '.files' >"$OUT_DIR/plan_files.json" 2>/dev/null || true
    if [ -s "$OUT_DIR/plan_files.json" ]; then pf_args=(--plan-files "$OUT_DIR/plan_files.json"); fi
  fi
  decision="$(uvpy -m factory.policy merge-policy --pr "$fresh" --factory "$INPUTS_DIR/factory.json" "${pf_args[@]}" --trigger "$trigger")"
  merge="$(jq -r '.merge' <<<"$decision")"
  log "merge-policy: $(jq -c '.' <<<"$decision")"

  if [ "$merge" = "true" ]; then
    gh pr merge "$PR" --repo "$TARGET_REPO" --squash --delete-branch
    linked="$(jq -r '.body | capture("(?i)(closes|fixes|resolves) #(?<n>[0-9]+)") | .n' "$fresh" 2>/dev/null || true)"
    if [ -n "$linked" ]; then edit_labels issue "$linked" "status:done" "status:in-review"; fi
    return 0
  fi

  if [ "$(jq -r '.ci_fix.attempted' "$f")" = "true" ]; then
    if jq -e 'any(.labels[]; . == "ci-fix:attempted")' "$fresh" >/dev/null; then
      log "ci-fix already attempted; handing to a human"
      edit_labels pr "$PR" "needs-human" ""
    else
      do_ci_fix "$f" "$fresh"
    fi
  else
    log "not mergeable: nothing more to do"
  fi
}

do_ci_fix() { # <proposal> <fresh pr.json>
  local f=$1 fresh=$2 head msg fmt
  head="$(jq -r '.headRefName' "$fresh")"
  if ! [[ "$head" =~ ^(feat|fix)/[0-9]+-[a-z0-9-]+$ ]]; then
    warn "ci-fix only on factory branches ($head); labelling needs-human"
    edit_labels pr "$PR" "needs-human" ""
    return 0
  fi
  fmt="$(jq -r '.toolchain.format // empty' "$INPUTS_DIR/factory.json")"
  if [ -z "$fmt" ]; then
    warn "ci-fix: profile has no toolchain.format; labelling needs-human"
    edit_labels pr "$PR" "needs-human" ""
    return 0
  fi
  (cd "$WORK_DIR" && env -u GH_TOKEN -u GITHUB_TOKEN sh -c "$fmt") || warn "toolchain.format failed"
  if [ -z "$(git -C "$WORK_DIR" status --porcelain)" ]; then
    warn "ci-fix produced no changes"
    edit_labels pr "$PR" "ci-fix:attempted,needs-human" ""
    return 0
  fi
  git -C "$WORK_DIR" add -A
  guard_diff --cached
  msg="$(jq -r '.ci_fix.commit_message // "apply formatting fixes"' "$f")"
  git -C "$WORK_DIR" -c user.name="$APP_SLUG" -c user.email="$APP_SLUG@users.noreply.github.com" commit -q -m "[ci-fix] ${msg#\[ci-fix\] }"
  git_auth -C "$WORK_DIR" push origin "HEAD:refs/heads/$head"
  edit_labels pr "$PR" "ci-fix:attempted" ""
}

# ---------------------------------------------------------------- verifier
apply_verify() {
  local f; f="$(proposal_path verify)"; validate_and_guard verify "$f"
  local outcome title lbls num comment
  outcome="$(jq -r '.outcome' "$f")"
  if [ "$outcome" = "skipped" ]; then log "verifier skipped"; return 0; fi
  num=""
  if [ "$outcome" = "fail" ] && [ "$(jq '.bug != null' "$f")" = "true" ]; then
    title="$(jq -r '.bug.title' "$f")"
    jq -r '.bug.body' "$f" | sanitize >"$TMP/bug.md"
    lbls="$(jq -c '(.bug.labels // []) + ["bug", "status:backlog"]' "$f" | filter_labels | csv)"
    lbls="${lbls:+$lbls,}factory:accepted"
    num="$(create_issue "$title" "$TMP/bug.md" "$lbls")"
  fi
  if [ -n "$PR" ]; then
    comment="$(jq -r '.pr_comment // .evidence_md' "$f")"
    { printf '%s\n' "$VERIFY_MARKER"; printf '%s\n' "$comment"; if [ -n "$num" ]; then printf '\nFiled #%s\n' "$num"; fi; } | sanitize >"$TMP/vc.md"
    upsert_comment "$PR" "$VERIFY_MARKER" "$TMP/vc.md"
  else
    log "no PR to comment on"
  fi
}

# ---------------------------------------------------------------- incident responder
apply_incident() {
  local f; f="$(proposal_path respond)"; validate_and_guard respond "$f"
  local count i sig hash marker title prio lbls existing
  count="$(jq '.incidents | length' "$f")"
  if [ "$count" -gt 5 ]; then warn "incident: capping at 5 issues"; count=5; fi
  for ((i = 0; i < count; i++)); do
    existing="$(jq -r ".incidents[$i].existing_issue // empty" "$f")"
    if [ -n "$existing" ]; then log "incident $i already tracked by #$existing"; continue; fi
    sig="$(jq -r ".incidents[$i].signature" "$f")"
    hash="$(printf '%s' "$sig" | sha256sum | cut -c1-12)"
    marker="<!-- factory:incident:$hash -->"
    title="$(jq -r ".incidents[$i].title" "$f")"
    prio="$(jq -r ".incidents[$i].priority" "$f")"
    { printf '%s\n' "$marker"; jq -r ".incidents[$i].body" "$f"; } | sanitize >"$TMP/inc.md"
    lbls="$(jq -c ".incidents[$i].labels + [\"bug\", \"status:backlog\", \"priority:$prio\"]" "$f" | filter_labels | csv)"
    lbls="${lbls:+$lbls,}factory:accepted"
    create_issue "$title" "$TMP/inc.md" "$lbls" "$marker" >/dev/null
  done
}

# ---------------------------------------------------------------- dispatch
if [ "$MODE" = "--auto" ]; then
  phases=()
  if [ -s "$OUT_DIR/phases_ok.txt" ]; then read -r -a phases <"$OUT_DIR/phases_ok.txt"; fi
  if [ ${#phases[@]} -eq 0 ]; then log "no successful phases to apply"; exit 0; fi
else
  phases=("$MODE")
fi

for phase in "${phases[@]}"; do
  case "$AGENT:$phase" in
    triage:triage) apply_triage ;;
    builder:research|bug-fixer:research) log "research has no side effects" ;;
    builder:plan|bug-fixer:plan) apply_plan ;;
    builder:implement|bug-fixer:implement) apply_implement ;;
    reviewer:review) apply_review ;;
    verifier:verify) apply_verify ;;
    incident-responder:respond) apply_incident ;;
    *) die "no apply handler for $AGENT:$phase" ;;
  esac
done
