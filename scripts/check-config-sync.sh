#!/usr/bin/env bash
# check-config-sync.sh: asserts that values duplicated outside factory.config.json stay in sync with it.
#  1. control-plane/wrangler.jsonc vars: TARGET_REPO, FACTORY_REPO, TARGET_DEFAULT_BRANCH, FACTORY_REF,
#     DAILY_MODEL_CALLS, MAX_OPEN_FACTORY_PRS.
#  2. control-plane/src/config.ts AGENT_SPECS maxModelCalls / timeoutMinutes per agent equal the config
#     (agents with phase_overrides: sum over their phases).
#  3. The inline timeout-minutes map in .github/workflows/agent-run.yml: for every agent,
#     map[agent] >= timeout_minutes + 5 (headroom for checkout, install and apply steps), and the map has
#     exactly the agents in the config. The map is a ceiling, not an equality, because the verifier also waits
#     for a deploy (up to 10 minutes) outside the model budget.
# Exit 1 with a readable list of mismatches.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="${CONFIG:-$ROOT/factory.config.json}"
WRANGLER="$ROOT/control-plane/wrangler.jsonc"
CONFIG_TS="$ROOT/control-plane/src/config.ts"
WORKFLOW="$ROOT/.github/workflows/agent-run.yml"
HEADROOM=5
fail=0
bad() { printf 'MISMATCH: %s\n' "$*" >&2; fail=1; }

# --- 1. wrangler vars (strip // comments, then pull "KEY": "value")
wvar() { sed 's://.*$::' "$WRANGLER" | sed -nE 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' | head -n1; }
check_var() { # <var> <expected>
  local got; got="$(wvar "$1")"
  if [ "$got" != "$2" ]; then bad "wrangler.jsonc var $1: got '${got:-<missing>}', config says '$2'"; fi
}
check_var TARGET_REPO "$(jq -r '"\(.target.owner)/\(.target.repo)"' "$CONFIG")"
check_var FACTORY_REPO "$(jq -r '"\(.factory.owner)/\(.factory.repo)"' "$CONFIG")"
check_var TARGET_DEFAULT_BRANCH "$(jq -r '.target.default_branch' "$CONFIG")"
check_var FACTORY_REF "$(jq -r '.factory.ref' "$CONFIG")"
check_var DAILY_MODEL_CALLS "$(jq -r '.quota.daily_model_calls' "$CONFIG")"
check_var MAX_OPEN_FACTORY_PRS "$(jq -r '.quota.max_open_factory_prs' "$CONFIG")"

# --- expected per-agent budgets from the config: "<agent> <calls> <minutes>"
expected="$(jq -r '.agents | to_entries[] | .key as $k | .value
  | (if .phase_overrides then ([.phase_overrides[] | .max_model_calls // 0] | add) else .max_model_calls end) as $c
  | (if .phase_overrides then ([.phase_overrides[] | .timeout_minutes // 0] | add) else .timeout_minutes end) as $t
  | "\($k) \($c) \($t)"' "$CONFIG")"

# --- 2. control-plane AGENT_SPECS. Lines look like: builder: { maxModelCalls: 25 + 15 + 60, timeoutMinutes: 10 + 10 + 40 },
ts_sum() { tr -d ' ' <<<"$1" | awk -F+ '{s=0; for (i=1;i<=NF;i++) s+=$i; print s}'; }
while read -r agent calls mins; do
  line="$(sed -n '/AGENT_SPECS/,/^};/p' "$CONFIG_TS" | grep -E "^[[:space:]]*\"?$agent\"?:" | head -n1 || true)"
  if [ -z "$line" ]; then bad "config.ts AGENT_SPECS has no entry for $agent"; continue; fi
  c="$(sed -nE 's/.*maxModelCalls:[[:space:]]*([0-9 +]+),.*/\1/p' <<<"$line")"
  t="$(sed -nE 's/.*timeoutMinutes:[[:space:]]*([0-9 +]+)[[:space:]]*\}.*/\1/p' <<<"$line")"
  [ -n "$c" ] && [ -n "$t" ] || { bad "config.ts AGENT_SPECS entry for $agent could not be parsed: $line"; continue; }
  gc="$(ts_sum "$c")"; gt="$(ts_sum "$t")"
  if [ "$gc" != "$calls" ]; then bad "config.ts $agent maxModelCalls: got $gc, config says $calls"; fi
  if [ "$gt" != "$mins" ]; then bad "config.ts $agent timeoutMinutes: got $gt, config says $mins"; fi
done <<<"$expected"
# no extra agents in config.ts
ts_agents="$(sed -n '/AGENT_SPECS/,/^};/p' "$CONFIG_TS" | sed -nE 's/^[[:space:]]*"?([a-z-]+)"?:[[:space:]]*\{.*maxModelCalls.*/\1/p' | sort)"
cfg_agents="$(jq -r '.agents | keys[]' "$CONFIG" | sort)"
if [ "$ts_agents" != "$cfg_agents" ]; then bad "agent set differs between config.ts and factory.config.json:"$'\n'"$(diff <(echo "$ts_agents") <(echo "$cfg_agents") || true)"; fi

# --- 3. workflow timeout map
map="$(sed -nE "s/.*timeout-minutes:.*fromJSON\('(\{[^']*\})'\).*/\1/p" "$WORKFLOW" | head -n1)"
if [ -z "$map" ] || ! jq -e . >/dev/null 2>&1 <<<"$map"; then
  bad "agent-run.yml: could not find the inline timeout-minutes fromJSON map"
else
  while read -r agent _ mins; do
    got="$(jq -r --arg a "$agent" '.[$a] // empty' <<<"$map")"
    need=$((mins + HEADROOM))
    if [ -z "$got" ]; then bad "agent-run.yml timeout map has no entry for $agent (need >= $need)"
    elif [ "$got" -lt "$need" ]; then bad "agent-run.yml timeout for $agent: $got < timeout_minutes ($mins) + $HEADROOM"; fi
  done <<<"$expected"
  extra="$(jq -r --argjson cfg "$(jq -c '.agents | keys' "$CONFIG")" 'keys - $cfg | .[]' <<<"$map")"
  if [ -n "$extra" ]; then bad "agent-run.yml timeout map has agents not in config: $extra"; fi
fi

if [ "$fail" -ne 0 ]; then echo "config sync check FAILED" >&2; exit 1; fi
echo "config sync OK"
