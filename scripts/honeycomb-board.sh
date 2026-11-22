#!/usr/bin/env bash
# honeycomb-board.sh [--dry-run]: apply docs/honeycomb/ (derived columns, queries, annotations, board) to Honeycomb.
# Owner-run. Needs HONEYCOMB_CONFIG_KEY (a configuration key); --dry-run needs none and makes no request.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DRY=false
case "${1:-}" in
  "") ;;
  --dry-run) DRY=true ;;
  *) die "usage: honeycomb-board.sh [--dry-run]" ;;
esac

DIR="${HONEYCOMB_BOARD_DIR:-$FACTORY_ROOT/docs/honeycomb}"
API_BASE="$(jq -r '.honeycomb.api_base' "$CONFIG")"
API_BASE="${API_BASE%/}"
if [ "$DRY" != true ]; then : "${HONEYCOMB_CONFIG_KEY:?HONEYCOMB_CONFIG_KEY required (use --dry-run to print the requests)}"; fi

# hc METHOD PATH [BODY]: prints the response body. Exit 44 on a 404 when ALLOW_404 is set; dies on any other failure.
# In --dry-run prints "METHOD path" and the compact body instead; the caller supplies the placeholder response.
exec 3>&1 # request lines go to the real stdout even from inside $(...)
ALLOW_404=""
hc() {
  local method=$1 path=$2 body=${3:-} args out code
  if [ "$DRY" = true ]; then
    printf '%s %s\n' "$method" "$path" >&3
    if [ -n "$body" ]; then printf '%s\n' "$body" | jq -c . >&3; fi
    return 0
  fi
  args=(-sS --max-time 30 -K - -X "$method" -H 'Content-Type: application/json' -w '\n%{http_code}')
  if [ -n "$body" ]; then args+=(--data "$body"); fi
  out="$(printf 'header = "X-Honeycomb-Team: %s"\n' "$HONEYCOMB_CONFIG_KEY" | curl "${args[@]}" "$API_BASE$path")" \
    || die "$method $path failed (curl)"
  code="${out##*$'\n'}"
  out="${out%$'\n'*}"
  case "$code" in
    2??) printf '%s\n' "$out" ;;
    404) if [ -n "$ALLOW_404" ]; then return 44; fi
         die "$method $path failed: HTTP 404 ${out:0:300}" ;;
    *) die "$method $path failed: HTTP $code ${out:0:300}" ;;
  esac
}

# Column check: list each dataset's columns once; skip what references a column that does not exist yet.
# --dry-run makes no request, so every column counts as present.
DATASETS="$( { echo factory; jq -r '.[].dataset' "$DIR/derived_columns.json"; jq -r '.dataset' "$DIR"/panels/*.json; } | sort -u)"
for ds in $DATASETS; do
  cols='[]'
  if [ "$DRY" = true ]; then
    hc GET "/1/columns/$ds" >/dev/null
  else
    rc=0
    resp="$(ALLOW_404=1 hc GET "/1/columns/$ds")" || rc=$?
    if [ "$rc" -eq 0 ]; then cols="$(jq -c '[.[].key_name]' <<<"$resp")"
    elif [ "$rc" -ne 44 ]; then exit "$rc"
    fi
  fi
  printf -v "COLS_${ds//-/_}" '%s' "$cols"
done
# missing DATASET REFS_JSON: prints the refs that are neither a column of the dataset nor an alias applied this run.
APPLIED='[]' # ["dataset:alias", ...]
missing() {
  local var="COLS_${1//-/_}"
  if [ "$DRY" = true ]; then return 0; fi
  jq -r --argjson cols "${!var}" --argjson applied "$APPLIED" --arg ds "$1" \
    '.[] | select(. as $r | ($cols | index($r) | not) and ($applied | index($ds + ":" + $r) | not))' <<<"$2"
}
SKIPPED=()

# Derived columns: upsert by alias.
n="$(jq 'length' "$DIR/derived_columns.json")"
for ((i = 0; i < n; i++)); do
  entry="$(jq -c ".[$i]" "$DIR/derived_columns.json")"
  ds="$(jq -r '.dataset' <<<"$entry")"
  col="$(jq -c 'del(.dataset)' <<<"$entry")"
  alias="$(jq -r '.alias' <<<"$col")"
  refs="$(jq -c '[.expression | match("\\$(?:\"([^\"]+)\"|([A-Za-z0-9_.]+))"; "g") | (.captures[0].string // .captures[1].string)] | unique' <<<"$col")"
  miss="$(missing "$ds" "$refs" | paste -sd, -)"
  if [ -n "$miss" ]; then
    warn "skipping derived column $alias ($ds): missing columns $miss"
    SKIPPED+=("derived:$alias")
    continue
  fi
  APPLIED="$(jq -c --arg a "$ds:$alias" '. + [$a]' <<<"$APPLIED")"
  rc=0
  existing="$(ALLOW_404=1 hc GET "/1/derived_columns/$ds?alias=$alias")" || rc=$?
  if [ "$DRY" = true ]; then rc=44; fi
  if [ "$rc" -eq 44 ]; then
    hc POST "/1/derived_columns/$ds" "$col" >/dev/null
  elif [ "$rc" -ne 0 ]; then
    exit "$rc"
  elif [ "$(jq -n --argjson a "$col" --argjson b "$existing" '($a.expression == $b.expression) and (($a.description // "") == ($b.description // ""))')" = "true" ]; then
    log "derived column $alias unchanged"
  else
    hc PUT "/1/derived_columns/$ds/$(jq -r '.id' <<<"$existing")" "$col" >/dev/null
  fi
done

# Queries and annotations: always fresh (queries are immutable), one per applicable panel.
panels='[]'
new_annotations='[]'
total=0
applied=0
for f in "$DIR"/panels/*.json; do
  total=$((total + 1))
  base="$(basename "$f" .json)"
  panel="$(jq -c . "$f")"
  ds="$(jq -r '.dataset' <<<"$panel")"
  refs="$(jq -c '[.query | (.calculations[]?.column?, .filters[]?.column?, .breakdowns[]?, .orders[]?.column?) | select(. != null)] | unique' <<<"$panel")"
  miss="$(missing "$ds" "$refs" | paste -sd, -)"
  if [ -n "$miss" ]; then
    warn "skipping panel $base ($ds): missing columns $miss"
    SKIPPED+=("$base")
    continue
  fi
  applied=$((applied + 1))
  qid="<query:$base>"
  aid="<annotation:$base>"
  q="$(hc POST "/1/queries/$ds" "$(jq -c .query <<<"$panel")")"
  if [ "$DRY" != true ]; then qid="$(jq -er '.id' <<<"$q")"; fi
  a="$(hc POST "/1/query_annotations/$ds" "$(jq -nc --arg q "$qid" --argjson p "$panel" \
    '{name: $p.name, description: $p.description, query_id: $q}')")"
  if [ "$DRY" != true ]; then aid="$(jq -er '.id' <<<"$a")"; fi
  new_annotations="$(jq -c --arg a "$aid" '. + [$a]' <<<"$new_annotations")"
  panels="$(jq -c --arg q "$qid" --arg a "$aid" --argjson p "$panel" \
    '. + [{type: "query",
           query_panel: ({query_id: $q, query_annotation_id: $a, query_style: $p.query_style}
                         + ($p.visualization_settings | if . then {visualization_settings: .} else {} end)),
           position: $p.position}]' <<<"$panels")"
done
log "applied $applied of $total panels${SKIPPED[*]:+; skipped: ${SKIPPED[*]}}"
if [ "$applied" -eq 0 ]; then
  warn "no panel is applicable yet (no events with the needed columns); board not touched"
  exit 0
fi

# preset_filters: only columns that exist in factory (or were applied as derived columns).
presets="$(jq -c '.preset_filters' "$DIR/board.json")"
miss_presets="$(missing factory "$(jq -c '[.[].column]' <<<"$presets")" | paste -sd, -)"
if [ -n "$miss_presets" ]; then
  warn "dropping preset filters on missing columns: $miss_presets"
  presets="$(jq -c --arg m "$miss_presets" '($m | split(",")) as $x | map(select(.column as $c | $x | index($c) | not))' <<<"$presets")"
fi

# Board: create when board.json has no id, else replace and drop the old panels' annotations.
board_id="$(jq -r '.id // empty' "$DIR/board.json")"
body="$(jq -c --argjson panels "$panels" --argjson presets "$presets" \
  '{name, description, type, layout_generation, preset_filters: $presets, panels: $panels}' "$DIR/board.json")"
if [ -z "$board_id" ]; then
  resp="$(hc POST /1/boards "$body")"
  if [ "$DRY" != true ]; then
    board_id="$(jq -er '.id' <<<"$resp")"
    tmp="$(mktemp)"
    jq --arg id "$board_id" '.id = $id' "$DIR/board.json" >"$tmp" && mv "$tmp" "$DIR/board.json"
    log "board id $board_id written to $DIR/board.json"
  fi
else
  old="$(hc GET "/1/boards/$board_id")"
  if [ "$DRY" = true ]; then old='{"panels": []}'; fi
  resp="$(hc PUT "/1/boards/$board_id" "$(jq -c --arg id "$board_id" '. + {id: $id}' <<<"$body")")"
  while read -r ads aid; do
    hc DELETE "/1/query_annotations/$ads/$aid" >/dev/null
  done < <(jq -r --argjson new "$new_annotations" \
    '[.panels[]?.query_panel | select(.query_annotation_id != null) | {ds: (.dataset // "factory"), id: .query_annotation_id}]
     | unique | .[] | select(.id as $x | $new | index($x) | not) | "\(.ds) \(.id)"' <<<"$old")
fi
if [ "$DRY" != true ]; then jq -r '"board: " + (.links.board_url // "<no board_url in response>")' <<<"$resp"; else log "dry run: no requests made"; fi
