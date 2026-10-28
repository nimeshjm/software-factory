#!/usr/bin/env bash
# honeycomb-query.sh: run liveness / error queries via the Honeycomb Query Data API -> inputs/honeycomb.json.
# api_base comes from factory.config.json; enabled, dataset, environment, liveness and errors come from the
# normalised target profile (inputs/factory.json .honeycomb). Filters are passed through verbatim.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OUT="$INPUTS_DIR/honeycomb.json"
PROFILE="$INPUTS_DIR/factory.json"
AGENT="${AGENT:-}"
mkdir -p "$INPUTS_DIR"

if [ "$(jq -r '.honeycomb.enabled // false' "$PROFILE")" != "true" ]; then
  jq -n '{enabled: false, liveness: null, errors: []}' >"$OUT"
  exit 0
fi
: "${HONEYCOMB_QUERY_KEY:?HONEYCOMB_QUERY_KEY required}"
base="$(jq -r '.honeycomb.api_base' "$CONFIG")"
dataset="$(jq -r '.honeycomb.dataset // empty' "$PROFILE")"
[ -n "$dataset" ] || die "profile honeycomb.enabled is true but honeycomb.dataset is not set"
environment="$(jq -r '.honeycomb.environment // empty' "$PROFILE")"
# The environment is implied by the query key; it is logged so a mismatch is easy to spot.
log "honeycomb: dataset=$dataset environment=${environment:-<unspecified>}"

hc() {
  local method=$1 path=$2 data=${3:-}
  local args=(-sS --fail-with-body --max-time 30 -X "$method" -K - -H 'Content-Type: application/json')
  if [ -n "$data" ]; then args+=(--data "$data"); fi
  printf 'header = "X-Honeycomb-Team: %s"\n' "$HONEYCOMB_QUERY_KEY" | curl "${args[@]}" "$base$path"
}

# run_query <spec-json>: stdout compact array of result rows (.data objects)
run_query() {
  local qid rid res
  qid="$(hc POST "/1/queries/$dataset" "$1" | jq -r '.id')"
  rid="$(hc POST "/1/query_results/$dataset" "$(jq -nc --arg q "$qid" '{query_id: $q, disable_series: true}')" | jq -r '.id')"
  for _ in $(seq 1 30); do
    res="$(hc GET "/1/query_results/$dataset/$rid")"
    if [ "$(jq -r '.complete // false' <<<"$res")" = "true" ]; then
      jq -c '[.data.results[]?.data]' <<<"$res"
      return 0
    fi
    sleep 2
  done
  echo "query $rid did not complete" >&2
  return 1
}

collect() {
  local live='null' errors='[]' spec rows win now start
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$AGENT" != "incident-responder" ]; then
    spec="$(jq -c '.honeycomb.liveness | {calculations: [{op: "COUNT"}], filters: .filters, filter_combination: "AND", time_range: (.window_minutes * 60)}' "$PROFILE")"
    rows="$(run_query "$spec")"
    win="$(jq -r '.honeycomb.liveness.window_minutes' "$PROFILE")"
    live="$(jq -c --argjson w "$win" '{events: (.[0].COUNT // 0), window_minutes: $w}' <<<"$rows")"
  fi
  if [ "$AGENT" != "verifier" ]; then
    for key in group_by fallback_group_by; do
      spec="$(jq -c --arg k "$key" '.honeycomb.errors | {calculations: [{op: "COUNT"}], filters: .filters, filter_combination: "AND",
              breaks: .[$k], orders: [{op: "COUNT", order: "descending"}], limit: 50, time_range: (.window_minutes * 60)}' "$PROFILE")"
      rows="$(run_query "$spec")"
      start="$(date -u -d "-$(jq -r '.honeycomb.errors.window_minutes' "$PROFILE") minutes" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "$now")"
      errors="$(jq -c --arg k "$key" --arg s "$start" --arg n "$now" --slurpfile cfg "$PROFILE" '
        ($cfg[0].honeycomb.errors[$k]) as $cols
        | map(. as $r | {signature: ([$cols[] | ($r[.] // "" | tostring)] | join("|")), count: ($r.COUNT // 0),
                         first_seen: $s, last_seen: $n, sample: ($r | del(.COUNT))})
        | map(select(.signature | gsub("\\|"; "") | length > 0))' <<<"$rows")"
      if [ "$(jq 'length' <<<"$errors")" -gt 0 ]; then break; fi
    done
  fi
  jq -n --argjson l "$live" --argjson e "$errors" '{enabled: true, liveness: $l, errors: $e}'
}

if result="$(collect)"; then
  printf '%s\n' "$result" >"$OUT"
else
  if [ "$AGENT" = "incident-responder" ]; then die "honeycomb query failed"; fi
  warn "honeycomb query failed; continuing with error marker"
  jq -n '{enabled: true, liveness: null, errors: [], error: "honeycomb query failed"}' >"$OUT"
fi
