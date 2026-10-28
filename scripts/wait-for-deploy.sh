#!/usr/bin/env bash
# wait-for-deploy.sh: poll ${TARGET_BASE_URL}${smoke.version.path} until .<sha_field> == EXPECTED_SHA or timeout.
# Path, sha_field and auth_header come from inputs/factory.json (.smoke.version); the header value is
# SMOKE_AUTH_TOKEN (header omitted when unset). Writes inputs/deploy.json ({"skipped": true} when the
# profile has no smoke.version). Always exits 0 so the verifier can report a mismatch.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROFILE="$INPUTS_DIR/factory.json"
mkdir -p "$INPUTS_DIR"
if [ "$(jq -r '.smoke.version == null' "$PROFILE")" = "true" ]; then
  jq -n '{skipped: true}' >"$INPUTS_DIR/deploy.json"
  exit 0
fi

EXPECTED_SHA="${EXPECTED_SHA:?EXPECTED_SHA required}"
VERSION_PATH="$(jq -r '.smoke.version.path' "$PROFILE")"
SHA_FIELD="$(jq -r '.smoke.version.sha_field // "sha"' "$PROFILE")"
AUTH_HEADER="$(jq -r '.smoke.version.auth_header // empty' "$PROFILE")"
TIMEOUT_S="${TIMEOUT_S:-600}"
INTERVAL_S="${INTERVAL_S:-20}"
BASE="${TARGET_BASE_URL:-}"
url="${BASE}${VERSION_PATH}"
observed=""
matched=false

if [ -z "$BASE" ]; then
  warn "TARGET_BASE_URL unset; cannot poll deploy"
else
  while :; do
    if [ -n "$AUTH_HEADER" ] && [ -n "${SMOKE_AUTH_TOKEN:-}" ]; then
      body="$(printf 'header = "%s: %s"\n' "$AUTH_HEADER" "$SMOKE_AUTH_TOKEN" \
        | curl -sS --max-time 15 -K - "$url" 2>/dev/null || true)"
    else
      body="$(curl -sS --max-time 15 "$url" 2>/dev/null || true)"
    fi
    observed="$(jq -r --arg f "$SHA_FIELD" '.[$f] // empty' <<<"$body" 2>/dev/null || true)"
    if [ -n "$observed" ] && [ "$observed" = "$EXPECTED_SHA" ]; then matched=true; break; fi
    if [ "$SECONDS" -ge "$TIMEOUT_S" ]; then break; fi
    sleep "$INTERVAL_S"
  done
fi

jq -n --arg exp "$EXPECTED_SHA" --arg obs "$observed" --argjson m "$matched" --argjson el "$SECONDS" --arg url "$url" \
  '{expected_sha: $exp, observed_sha: (if $obs == "" then null else $obs end), matched: $m, elapsed_s: $el, version_url: $url}' \
  >"$INPUTS_DIR/deploy.json"
