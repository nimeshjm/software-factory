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

# Validation first, no writes: columns and derived columns per dataset, the board (by id, or by name).
# --dry-run makes no request, so every column counts as present and every list as empty.
DATASETS="$( { echo factory; jq -r '.[].dataset' "$DIR/derived_columns.json"; jq -r '.dataset // empty' "$DIR"/panels/*.json; } | sort -u)"
# list_of PATH: the response array, or [] on 404 and in --dry-run.
list_of() {
  local resp rc=0
  if [ "$DRY" = true ]; then hc GET "$1" >/dev/null; echo '[]'; return 0; fi
  resp="$(ALLOW_404=1 hc GET "$1")" || rc=$?
  if [ "$rc" -eq 0 ]; then printf '%s\n' "$resp"; elif [ "$rc" -eq 44 ]; then echo '[]'; else return "$rc"; fi
}
for ds in $DATASETS; do
  v="${ds//-/_}"
  printf -v "COLS_$v" '%s' "$(list_of "/1/columns/$ds" | jq -c '[.[].key_name]')"
  printf -v "DERIVED_$v" '%s' "$(list_of "/1/derived_columns/$ds" | jq -c '.')"
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

# Derived columns: decide skip, conflict, unchanged, update or create (writes come later).
DC_ENTRIES=() DC_IDS=() DC_ACTIONS=()
n="$(jq 'length' "$DIR/derived_columns.json")"
for ((i = 0; i < n; i++)); do
  entry="$(jq -c ".[$i]" "$DIR/derived_columns.json")"
  ds="$(jq -r '.dataset' <<<"$entry")"
  col="$(jq -c 'del(.dataset)' <<<"$entry")"
  alias="$(jq -r '.alias' <<<"$col")"
  v="${ds//-/_}"; colsvar="COLS_$v"; dervar="DERIVED_$v"
  existing="$(jq -c --arg a "$alias" 'map(select(.alias == $a)) | first // empty' <<<"${!dervar}")"
  if [ -z "$existing" ] && [ "$(jq --arg a "$alias" 'index($a) != null' <<<"${!colsvar}")" = "true" ]; then
    die "derived column alias $alias ($ds) is the name of a real column; rename it in derived_columns.json (and the panels that use it)"
  fi
  refs="$(jq -c '[.expression | match("\\$(?:\"([^\"]+)\"|([A-Za-z0-9_.]+))"; "g") | (.captures[0].string // .captures[1].string)] | unique' <<<"$col")"
  miss="$(missing "$ds" "$refs" | paste -sd, -)"
  if [ -n "$miss" ]; then
    warn "skipping derived column $alias ($ds): missing columns $miss"
    SKIPPED+=("derived:$alias")
    continue
  fi
  APPLIED="$(jq -c --arg a "$ds:$alias" '. + [$a]' <<<"$APPLIED")"
  action=create
  if [ -n "$existing" ]; then
    if [ "$(jq -n --argjson a "$col" --argjson b "$existing" '($a.expression == $b.expression) and (($a.description // "") == ($b.description // ""))')" = "true" ]; then
      action=unchanged
    else
      action=update
    fi
  fi
  eid=""
  if [ -n "$existing" ]; then eid="$(jq -r '.id' <<<"$existing")"; fi
  DC_ENTRIES+=("$entry"); DC_IDS+=("$eid"); DC_ACTIONS+=("$action")
done

# Board: by id (must exist) or by name (at most one).
board_id="$(jq -r '.id // empty' "$DIR/board.json")"
board_name="$(jq -r '.name' "$DIR/board.json")"
if [ -n "$board_id" ]; then
  rc=0
  ALLOW_404=1 hc GET "/1/boards/$board_id" >/dev/null || rc=$?
  if [ "$rc" -eq 44 ]; then die "board $board_id not found; set id to null in board.json to create it"; fi
  if [ "$rc" -ne 0 ]; then exit "$rc"; fi
else
  found="$(list_of /1/boards | jq -c --arg n "$board_name" '[.[] | select(.name == $n) | .id]')"
  case "$(jq 'length' <<<"$found")" in
    0) ;;
    1) board_id="$(jq -r '.[0]' <<<"$found")"; log "reusing existing board $board_id named $board_name" ;;
    *) die "several boards are named $board_name ($(jq -r 'join(", ")' <<<"$found")); delete the extras or set id in board.json" ;;
  esac
fi

# Derived columns: writes.
for i in "${!DC_ENTRIES[@]}"; do
  ds="$(jq -r '.dataset' <<<"${DC_ENTRIES[$i]}")"
  col="$(jq -c 'del(.dataset)' <<<"${DC_ENTRIES[$i]}")"
  case "${DC_ACTIONS[$i]}" in
    create) hc POST "/1/derived_columns/$ds" "$col" >/dev/null ;;
    update) hc PUT "/1/derived_columns/$ds/${DC_IDS[$i]}" "$col" >/dev/null ;;
    *) log "derived column $(jq -r '.alias' <<<"$col") unchanged" ;;
  esac
done

# Queries and annotations: always fresh (queries are immutable), one per applicable panel.
panels='[]'
new_annotations='[]'
panel_names='[]'
query_datasets=""
total=0
applied=0
for f in "$DIR"/panels/*.json; do
  base="$(basename "$f" .json)"
  panel="$(jq -c . "$f")"
  if [ "$(jq -r '.type // "query"' <<<"$panel")" = "text" ]; then
    # Text panels are always on the board: no dataset, query, annotation or column check.
    panels="$(jq -c --argjson p "$panel" '. + [{type: "text", text_panel: {content: $p.content}, position: $p.position}]' <<<"$panels")"
    continue
  fi
  total=$((total + 1))
  ds="$(jq -r '.dataset' <<<"$panel")"
  panel_names="$(jq -c --argjson p "$panel" '. + [$p.name]' <<<"$panel_names")"
  query_datasets="$(printf '%s\n%s\n' "$query_datasets" "$ds" | sort -u | sed '/^$/d')"
  refs="$(jq -c '[.query | (.calculations[]?.column?, .filters[]?.column?, .breakdowns[]?, .orders[]?.column?, .havings[]?.column?) | select(. != null)] | unique' <<<"$panel")"
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

# Board: PUT when it exists (by id or by name), else create; write a new or found id back.
body="$(jq -c --argjson panels "$panels" --argjson presets "$presets" \
  '{name, description, type, layout_generation, preset_filters: $presets, panels: $panels}' "$DIR/board.json")"
if [ -z "$board_id" ]; then
  resp="$(hc POST /1/boards "$body")"
  if [ "$DRY" != true ]; then board_id="$(jq -er '.id' <<<"$resp")"; fi
else
  resp="$(hc PUT "/1/boards/$board_id" "$(jq -c --arg id "$board_id" '. + {id: $id}' <<<"$body")")"
fi
if [ "$DRY" != true ] && [ "$board_id" != "$(jq -r '.id // empty' "$DIR/board.json")" ]; then
  tmp="$(mktemp)"
  jq --arg id "$board_id" '.id = $id' "$DIR/board.json" >"$tmp" && mv "$tmp" "$DIR/board.json"
  log "board id $board_id written to $DIR/board.json"
fi

# Annotation cleanup: ours (by panel name) and not on the board just written.
for ds in $query_datasets; do
  while IFS= read -r aid; do
    hc DELETE "/1/query_annotations/$ds/$aid" >/dev/null
  done < <(list_of "/1/query_annotations/$ds" | jq -r --argjson names "$panel_names" --argjson new "$new_annotations" \
    '.[] | select((.name as $n | $names | index($n)) and (.id as $i | $new | index($i) | not)) | .id')
done
if [ "$DRY" != true ]; then jq -r '"board: " + (.links.board_url // "<no board_url in response>")' <<<"$resp"; else log "dry run: no requests made"; fi
