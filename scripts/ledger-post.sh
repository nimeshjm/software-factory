#!/usr/bin/env bash
# ledger-post.sh started|finished: report to the control plane. Never fails the job.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

EVENT="${1:?usage: ledger-post.sh started|finished}"
RUN_ID="${RUN_ID:?RUN_ID required}"

post() {
  local path=$1 payload=$2
  if [ -z "${FACTORY_URL:-}" ] || [ -z "${FACTORY_TOKEN:-}" ]; then
    warn "FACTORY_URL/FACTORY_TOKEN not set; skipping ledger post"
    return 0
  fi
  if ! printf 'header = "Authorization: Bearer %s"\n' "$FACTORY_TOKEN" \
    | curl -sS --fail-with-body --max-time 15 --retry 2 -K - -X POST \
        -H 'Content-Type: application/json' --data "$payload" "${FACTORY_URL%/}/runs/$RUN_ID/$path" >/dev/null; then
    warn "ledger post $path failed (ignored)"
  fi
}

case "$EVENT" in
  started)
    payload="$(jq -n --arg a "${AGENT:-}" --arg p "${PHASE:-}" --arg u "${WORKFLOW_RUN_URL:-}" \
      '{agent: $a, phase: $p, workflow_run_url: $u}')"
    post started "$payload" || true ;;
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
                     chosen: ((map(select(.success != true)) | first) // last)}' "${results[@]}")"
      # phase = first failing phase, else the last one run; its dir name is the phase.
      bad_phase=""; last_phase=""
      for f in "${results[@]}"; do
        last_phase="$(basename "$(dirname "$f")")"
        if [ -z "$bad_phase" ] && [ "$(jq '.success == true' "$f")" != "true" ]; then bad_phase="$last_phase"; fi
      done
      phase="${bad_phase:-$last_phase}"
      if [ "$phase" = "out" ] || [ "$phase" = "$(basename "$OUT_DIR")" ]; then phase="${PHASE:-}"; fi
      payload="$(jq -n --argjson r "$agg" --arg job "${JOB_STATUS:-}" --arg pr "${PR:-}" --arg issue "${ISSUE:-}" --arg cpr "$created_pr" \
        --arg phase "$phase" --argjson dry "$dry_run" --arg verdict "$verdict" \
        '{status: (if $r.success and ($job == "success" or $job == "") then "success" else "failed" end),
          stop_reason: $r.stop_reason, model_calls: $r.model_calls, usage: $r.usage,
          phase: (if $phase == "" then null else $phase end),
          error: ($r.chosen.error | if type == "string" then .[:300] else null end),
          throttles: $r.throttles, dry_run: $dry,
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
