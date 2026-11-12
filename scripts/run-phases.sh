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
for phase in "${phase_list[@]}"; do
  export OTEL_RESOURCE_ATTRIBUTES="${OTEL_RESOURCE_ATTRIBUTES_BASE:-},factory.phase=${phase}"
  rm -f out/proposal.json out/result.json
  prc=0
  uv run --frozen python -m factory.run --agent "$AGENT" --phase "$phase" --inputs inputs/ --out out/ "${dry[@]}" || prc=$?
  mkdir -p "out/phases/$phase"
  for f in proposal.json result.json research.md plan.md; do
    if [ -f "out/$f" ]; then cp "out/$f" "out/phases/$phase/$f"; fi
  done
  if [ "$prc" -ne 0 ]; then
    echo "::error::harness failed for phase $phase (exit $prc)"
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
