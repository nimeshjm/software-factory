#!/usr/bin/env bash
# ledger-post.sh started|finished: report to the control plane. Never fails the job.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

EVENT="${1:?usage: ledger-post.sh started|finished}"
RUN_ID="${RUN_ID:?RUN_ID required}"

# post <path> <payload> [response file]: the response body goes to the file when given.
post() {
  local path=$1 payload=$2 resp=${3:-/dev/null}
  if [ -z "${FACTORY_URL:-}" ] || [ -z "${FACTORY_TOKEN:-}" ]; then
    warn "FACTORY_URL/FACTORY_TOKEN not set; skipping ledger post"
    return 0
  fi
  if ! printf 'header = "Authorization: Bearer %s"\n' "$FACTORY_TOKEN" \
    | curl -sS --fail-with-body --max-time 15 --retry 2 -K - -X POST \
        -H 'Content-Type: application/json' --data "$payload" "${FACTORY_URL%/}/runs/$RUN_ID/$path" >"$resp"; then
    warn "ledger post $path failed (ignored)"
  fi
}

case "$EVENT" in
  started)
    payload="$(jq -n --arg a "${AGENT:-}" --arg p "${PHASE:-}" --arg u "${WORKFLOW_RUN_URL:-}" \
      '{agent: $a, phase: $p, workflow_run_url: $u}')"
    # The control plane answers with this run's paid allowance (CONTRACTS.md, Daily paid budget); none = 0.
    mkdir -p "$OUT_DIR"
    resp="$(mktemp)"
    post started "$payload" "$resp" || true
    allowance="$(jq -r '(.paid_allowance_usd // 0) | tonumber? // 0' "$resp" 2>/dev/null || true)"
    printf '%s\n' "${allowance:-0}" >"$OUT_DIR/paid_allowance.txt"
    log "paid allowance: \$${allowance:-0}"
    rm -f "$resp" ;;
  finished)
    results=()
    for f in "$OUT_DIR"/phases/*/result.json; do if [ -f "$f" ]; then results+=("$f"); fi; done
    if [ ${#results[@]} -eq 0 ] && [ -f "$OUT_DIR/result.json" ]; then results=("$OUT_DIR/result.json"); fi
    # Phase order: PHASES (what run-phases.sh ran), else glob order of existing results.
    if [ -n "${PHASES:-}" ]; then
      results=()
      for ph in $PHASES; do
        if [ -f "$OUT_DIR/phases/$ph/result.json" ]; then results+=("$OUT_DIR/phases/$ph/result.json"); fi
      done
      if [ ${#results[@]} -eq 0 ] && [ -f "$OUT_DIR/result.json" ]; then results=("$OUT_DIR/result.json"); fi
    fi
    dry_run=false
    if [ "${DRY_RUN:-}" = "true" ]; then dry_run=true; fi
    verdict=""
    if [ "${AGENT:-}" = "reviewer" ] && [ -f "$OUT_DIR/phases/review/proposal.json" ]; then
      verdict="$(jq -r 'if (.verdict | type) == "string" then .verdict else "" end' "$OUT_DIR/phases/review/proposal.json" 2>/dev/null || true)"
    fi
    created_pr=""
    if [ -s "$OUT_DIR/created-pr.txt" ]; then created_pr="$(cat "$OUT_DIR/created-pr.txt")"; fi
    if [ ${#results[@]} -gt 0 ]; then
      agg="$(jq -s '{success: (all(.[]; .success == true)), stop_reason: (last.stop_reason),
                     model_calls: (map(.model_calls // 0) | add),
                     usage: {input_tokens: (map(.usage.input_tokens // 0) | add),
                             output_tokens: (map(.usage.output_tokens // 0) | add),
                             total_tokens: (map(.usage.total_tokens // 0) | add)},
                     throttles: (map(.throttles // 0) | add),
                     paid_cost_usd: (map(.paid_cost_usd // 0) | add),
                     free_key: (any(.[]; .free_key == true)),
                     chosen: ((map(select(.success != true)) | first) // last)}' "${results[@]}")"
      # phase = first failing phase, else the last one run; its dir name is the phase.
      bad_phase=""; last_phase=""
      for f in "${results[@]}"; do
        last_phase="$(basename "$(dirname "$f")")"
        if [ -z "$bad_phase" ] && [ "$(jq '.success == true' "$f")" != "true" ]; then bad_phase="$last_phase"; fi
      done
      phase="${bad_phase:-$last_phase}"
      if [ "$phase" = "out" ] || [ "$phase" = "$(basename "$OUT_DIR")" ]; then phase="${PHASE:-}"; fi
      # Error: harness error of the first failing phase, else the failed post-step's own message;
      # then up to two distinct tool_guard blocks (soft nudges are other guards) across the phases.
      step_error=""
      if [ -s "$OUT_DIR/step-error.txt" ]; then step_error="$(head -c 600 "$OUT_DIR/step-error.txt" | tr '\n' ' ' | sed 's/ *$//')"; fi
      blocked="$(jq -s -r '[.[].guard_events[]? | select(.guard == "tool_guard") | .detail | select(type == "string" and . != "")]
        | reduce .[] as $d ([]; if index([$d]) then . else . + [$d] end) | .[:2] | join("; blocked: ")' "${results[@]}" 2>/dev/null || true)"
      payload="$(jq -n --argjson r "$agg" --arg stepe "$step_error" --arg blocked "$blocked" --arg job "${JOB_STATUS:-}" --arg pr "${PR:-}" --arg issue "${ISSUE:-}" --arg cpr "$created_pr" \
        --arg phase "$phase" --argjson dry "$dry_run" --arg verdict "$verdict" \
        '{status: (if $r.success and ($job == "success" or $job == "") then "success" else "failed" end),
          stop_reason: $r.stop_reason, model_calls: $r.model_calls, usage: $r.usage,
          phase: (if $phase == "" then null else $phase end),
          error: (if ($r.success and ($job == "success" or $job == "")) then null
                  else (($r.chosen.error | if type == "string" then . else "" end) as $he
                    | (if $he != "" then $he
                       elif $r.success then (if $stepe != "" then "apply: " + $stepe else "post-step failed (see run log)" end)
                       else "" end) as $base
                    | ($base + (if $blocked == "" or ($base | contains("blocked: ")) then "" elif $base == "" then "blocked: " + $blocked else "; blocked: " + $blocked end))
                    | if . == "" then null else .[:300] end) end),
          throttles: $r.throttles, dry_run: $dry,
          paid_cost_usd: $r.paid_cost_usd, free_key: $r.free_key,
          verdict: (if $verdict == "" then null else $verdict end),
          pr_number: (if $cpr != "" then ($cpr | tonumber) elif $pr != "" then ($pr | tonumber) else null end),
          issue_number: (if $issue == "" then null else ($issue | tonumber) end)}')"
    else
      status=timeout; reason=timeout
      if [ "${DENIED:-}" = "true" ]; then status=denied; reason=author_gate; fi
      payload="$(jq -n --arg s "$status" --arg r "$reason" --arg pr "${PR:-}" --arg issue "${ISSUE:-}" \
        --arg phase "${PHASE:-}" --argjson dry "$dry_run" \
        '{status: $s, stop_reason: $r, model_calls: 0, usage: {input_tokens: 0, output_tokens: 0, total_tokens: 0},
          pr_number: (if $pr == "" then null else ($pr | tonumber) end),
          issue_number: (if $issue == "" then null else ($issue | tonumber) end),
          phase: (if $phase == "" then null else $phase end), error: null, throttles: 0, dry_run: $dry, verdict: null}')"
    fi
    post finished "$payload" || true ;;
  *) die "unknown event: $EVENT" ;;
esac
exit 0
