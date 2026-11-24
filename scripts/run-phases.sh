#!/usr/bin/env bash
# run-phases.sh: run the harness for each phase in $PHASES. Runs with a minimal env (no GitHub token).
# Stops after plan when `factory.policy plan-gate` says hold (needs_human, or a hold_on label without plan:approved).
set -euo pipefail

AGENT="${AGENT:?}"
PHASES="${PHASES:?}"
mkdir -p out/phases
: >out/phases_ok.txt
ok=()
rc=0
read -r -a phase_list <<<"$PHASES"
# --dry-run only labels result.json and the trace; the workflow skips apply on its own.
dry=()
if [ "${DRY_RUN:-}" = "true" ]; then dry=(--dry-run); fi
# Reviewer on an unchanged Dependabot head: apply would discard a new review, so reuse the posted verdict and
# let apply re-check merge policy without a model call (CONTRACTS.md, Reviewer on an unchanged head).
if [ "$AGENT" = reviewer ] && [ "$PHASES" = review ] && [ -f inputs/pr.json ] \
  && reused="$(uv run --frozen python -m factory.policy reuse-review --pr inputs/pr.json --head-sha "${HEAD_SHA:-}")"; then
  mkdir -p out/phases/review
  printf '%s\n' "$reused" >out/phases/review/proposal.json
  jq -n --arg run "${FACTORY_RUN_ID:-}" \
    '{run_id: $run, agent: "reviewer", phase: "review", success: true, stop_reason: "reused_review", model_calls: 0,
      usage: {input_tokens: 0, output_tokens: 0, total_tokens: 0}, cycles: 0, duration_s: 0, guard_events: [],
      tool_calls: [], throttles: 0, model_ids_used: [], error: null}' >out/phases/review/result.json
  echo review >out/phases_ok.txt
  echo "::notice::head ${HEAD_SHA:0:12} already has a factory review (verdict $(jq -r .verdict <<<"$reused")); no model call"
  exit 0
fi
# The run's paid allowance (out/paid_allowance.txt, from the control plane at "Ledger started"; none = 0) is
# shared by the phases: each one gets what the earlier ones left (CONTRACTS.md, Daily paid budget).
run_allowance="$(cat out/paid_allowance.txt 2>/dev/null || echo 0)"
paid_spent=0
for phase in "${phase_list[@]}"; do
  export OTEL_RESOURCE_ATTRIBUTES="${OTEL_RESOURCE_ATTRIBUTES_BASE:-},factory.phase=${phase}"
  PAID_ALLOWANCE_USD="$(jq -n --arg a "$run_allowance" --arg s "$paid_spent" \
    '[(($a | tonumber? // 0) - ($s | tonumber)), 0] | max')"
  export PAID_ALLOWANCE_USD
  rm -f out/proposal.json out/result.json
  prc=0
  uv run --frozen python -m factory.run --agent "$AGENT" --phase "$phase" --inputs inputs/ --out out/ ${dry[@]+"${dry[@]}"} || prc=$?
  mkdir -p "out/phases/$phase"
  for f in proposal.json result.json research.md plan.md; do
    if [ -f "out/$f" ]; then cp "out/$f" "out/phases/$phase/$f"; fi
  done
  if [ -f "out/phases/$phase/result.json" ]; then
    paid_spent="$(jq -r --arg s "$paid_spent" '($s | tonumber) + (.paid_cost_usd // 0)' "out/phases/$phase/result.json")"
  fi
  if [ "$prc" -ne 0 ]; then
    echo "::error::harness failed for phase $phase (exit $prc)"
    # Say why in the step log, so a dead run can be read without downloading the artifact.
    rj="out/phases/$phase/result.json"
    if [ -f "$rj" ]; then
      echo "::error::phase $phase result: $(jq -r '"stop_reason=\(.stop_reason // "?") model_calls=\(.model_calls // 0) total_tokens=\(.usage.total_tokens // 0) paid_usd=\(.paid_cost_usd // 0) models=\((.model_ids_used // []) | join(",")) switches=\((.model_switches // []) | map("\(.from)>\(.to) (\(.reason))") | join(", ")) error=\(.error // "")"' "$rj")"
    else
      echo "::error::phase $phase wrote no result.json"
    fi
    rc=$prc
    break
  fi
  ok+=("$phase")
  echo "${ok[*]}" >out/phases_ok.txt
  if [ "$phase" = "plan" ] && { [ "$AGENT" = builder ] || [ "$AGENT" = bug-fixer ]; }; then
    issue=()
    if [ -f inputs/issue.json ]; then issue=(--issue inputs/issue.json); fi
    gate="$(uv run --frozen python -m factory.policy plan-gate ${issue[@]+"${issue[@]}"} --factory inputs/factory.json --plan out/proposal.json)" || {
      echo "::error::plan-gate failed"; rc=1; break
    }
    if [ "$(jq -r '.hold' <<<"$gate")" = "true" ]; then
      echo "::notice::stopping after plan (held: $(jq -r '.reasons | join(", ")' <<<"$gate"))"
      break
    fi
  fi
done
exit "$rc"
