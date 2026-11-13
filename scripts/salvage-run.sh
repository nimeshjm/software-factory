#!/usr/bin/env bash
# salvage-run.sh: deterministic post-step for a failed builder or bug-fixer run. Never fails the job.
# It runs after apply-proposal.sh (so a plan that succeeded is already posted and labelled) and does
# four things, no model involved: finds the failed phase from out/phases/*/result.json; when that is
# `implement`, WIP-commits the uncommitted work in work/ and pushes it to backup/<N>-attempt<k>-<stop_reason>
# so the spent budget is not lost; moves status:in-progress back to status:backlog so the tick retries
# (the control plane's failure backoff spaces the retries); and posts one <!-- factory:failure --> comment.
# A failed `revise` phase (PR set) is different: no backup, no issue label reset; the PR's revise:requested
# is swapped for revise:attempted + needs-human and the failure comment goes on the PR.
# Env: RUN_ID, ISSUE, AGENT, GH_TOKEN (the apply token), RUN_URL (optional), PR (optional, revise only).
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_ID="${RUN_ID:?RUN_ID required}"
ISSUE="${ISSUE:?ISSUE required}"
AGENT="${AGENT:?AGENT required}"
: "${GH_TOKEN:?GH_TOKEN required}"
RUN_URL="${RUN_URL:-}"
PR="${PR:-}"
TRAILER="Co-Authored-By: software-factory <factory@users.noreply.github.com>"

salvage() {
  local phase="" p f stop_reason error branch="" action tmp
  for p in research plan implement revise; do
    f="$OUT_DIR/phases/$p/result.json"
    if [ -f "$f" ] && [ "$(jq -r '.success == true' "$f")" != "true" ]; then phase=$p; break; fi
  done
  if [ -z "$phase" ]; then log "salvage: no failed phase"; return 0; fi
  f="$OUT_DIR/phases/$phase/result.json"
  stop_reason="$(jq -r '.stop_reason // "unknown"' "$f")"
  error="$(jq -r '.error // ""' "$f" | tr '\n' ' ' | head -c 300 | sed 's/[[:space:]]*$//')"

  if [ "$phase" = "implement" ]; then
    branch="$(save_work "$stop_reason" || true)"
  fi

  local target=$ISSUE kind=issue
  if [ "$phase" = "revise" ] && [ -n "$PR" ]; then
    # The work is a PR branch: nothing is pushed to it, the one revise is spent, a human takes over.
    target=$PR kind=pr
    edit_labels pr "$PR" "revise:attempted,needs-human" "revise:requested" \
      || warn "salvage: PR label swap failed (ignored)"
    action='Labels swapped: revise:requested removed, revise:attempted and needs-human added.'
  elif gh issue view "$ISSUE" --repo "$TARGET_REPO" --json labels --jq '.labels[].name' | grep -qx 'status:in-progress'; then
    edit_labels issue "$ISSUE" "status:backlog" "status:in-progress" \
      || warn "salvage: label reset failed (ignored)"
    action='Label reset to status:backlog; the tick retries after the failure backoff.'
  else
    action='Labels left as they were.'
  fi

  tmp="$(mktemp)"
  {
    printf '<!-- factory:failure -->\n'
    printf 'The factory run %s failed in %s (%s): %s.\n\n' "$RUN_ID" "$phase" "$stop_reason" "$error"
    # shellcheck disable=SC2016
    if [ -n "$branch" ]; then printf 'Work saved on `%s`.\n\n' "$branch"; fi
    if [ "$kind" = pr ]; then printf 'No work was pushed to the PR branch.\n\n'; fi
    printf '%s\n\n' "$action"
    if [ -n "$RUN_URL" ]; then printf '_Run: %s_\n' "$RUN_URL"; fi
  } | sanitize >"$tmp"
  gh_retry gh "$kind" comment "$target" --repo "$TARGET_REPO" --body-file "$tmp" >/dev/null \
    || warn "salvage: failure comment failed (ignored)"
  rm -f "$tmp"
}

# save_work <stop_reason>: commit and push work/ as a backup branch; prints the branch name or nothing.
save_work() {
  local reason=$1 safe ahead k base="origin/$DEFAULT_BRANCH" n
  safe="$(printf '%s' "$reason" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_' '_')"
  if [ -z "$safe" ]; then safe=unknown; fi
  if [ -n "$(git -C "$WORK_DIR" status --porcelain)" ]; then
    git -C "$WORK_DIR" add -A >&2
    git -C "$WORK_DIR" -c user.name="$APP_SLUG" -c user.email="$APP_SLUG@users.noreply.github.com" \
      commit -q -m "WIP: salvage of $RUN_ID ($reason)

$TRAILER" >&2 || { warn "salvage: WIP commit failed"; return 0; }
  fi
  if ! git -C "$WORK_DIR" rev-parse -q --verify "$base" >/dev/null; then
    git_auth -C "$WORK_DIR" fetch -q origin "$DEFAULT_BRANCH" >&2 || true
  fi
  if git -C "$WORK_DIR" rev-parse -q --verify "$base" >/dev/null; then
    ahead="$(git -C "$WORK_DIR" rev-list --count "$base..HEAD")"
  else
    ahead="$(git -C "$WORK_DIR" rev-list --count HEAD 2>/dev/null || echo 0)"
  fi
  if [ "${ahead:-0}" -eq 0 ]; then log "salvage: nothing to save"; return 0; fi
  n="$(git_auth -C "$WORK_DIR" ls-remote --heads origin "refs/heads/backup/$ISSUE-attempt*" | wc -l)"
  k=$((n + 1))
  local branch="backup/$ISSUE-attempt$k-$safe"
  if gh_retry git_auth -C "$WORK_DIR" push -q origin "HEAD:refs/heads/$branch" >&2; then
    printf '%s\n' "$branch" >"$OUT_DIR/salvage.txt"
    log "salvaged: $branch"
    printf '%s' "$branch"
  else
    warn "salvage: push to $branch failed"
  fi
}

salvage || warn "salvage failed (ignored)"
exit 0
