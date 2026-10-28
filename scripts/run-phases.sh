#!/usr/bin/env bash
# run-phases.sh: run the harness for each phase in $PHASES. Runs with a minimal env (no GitHub token).
# Stops after plan when plan.needs_human or the issue is risk:high without plan:approved.
set -euo pipefail

AGENT="${AGENT:?}"
PHASES="${PHASES:?}"
mkdir -p out/phases
: >out/phases_ok.txt
ok=()
rc=0
read -r -a phase_list <<<"$PHASES"
for phase in "${phase_list[@]}"; do
  export OTEL_RESOURCE_ATTRIBUTES="${OTEL_RESOURCE_ATTRIBUTES_BASE:-},factory.phase=${phase}"
  rm -f out/proposal.json out/result.json
  prc=0
  uv run --frozen python -m factory.run --agent "$AGENT" --phase "$phase" --inputs inputs/ --out out/ || prc=$?
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
    needs_human="$(jq -r '.needs_human // false' out/proposal.json)"
    risk_high=false; approved=false
    if [ -f inputs/issue.json ]; then
      risk_high="$(jq -r 'any(.labels[]; . == "risk:high")' inputs/issue.json)"
      approved="$(jq -r 'any(.labels[]; . == "plan:approved")' inputs/issue.json)"
    fi
    if [ "$needs_human" = "true" ] || { [ "$risk_high" = "true" ] && [ "$approved" != "true" ]; }; then
      echo "::notice::stopping after plan (needs_human=$needs_human risk_high=$risk_high approved=$approved)"
      break
    fi
  fi
done
exit "$rc"
