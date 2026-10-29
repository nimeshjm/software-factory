#!/usr/bin/env bash
# preflight-labels.sh: idempotently create every label in factory.config.json on the target repo.
set -euo pipefail
# shellcheck source=lib.sh source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

jq -r '
  def color: if startswith("status:") then "1d76db" elif startswith("priority:") then "d93f0b"
    elif . == "bug" then "d73a4a" elif startswith("risk:") then "fbca04" elif startswith("size:") then "c5def5"
    elif . == "autonomy:dark" then "24292f" elif startswith("autonomy:") then "5319e7"
    elif . == "needs-human" then "b60205" elif . == "plan:approved" then "0e8a16"
    elif . == "ci-fix:attempted" then "e99695" elif . == "factory:accepted" then "0e8a16"
    elif . == "epic" then "3e4b9e" elif . == "spike" then "bfd4f2" else "ededed" end;
  def desc: {
    "status:backlog": "Ready for the factory to pick up", "status:plan-review": "Plan posted, waiting for plan:approved",
    "status:in-progress": "Factory is working on this", "status:in-review": "PR open, in review", "status:done": "Merged and done",
    "priority:1": "Highest priority", "priority:2": "Medium priority", "priority:3": "Low priority",
    "feature": "New capability", "enhancement": "Improvement to existing behaviour", "chore": "Maintenance work",
    "bug": "Something is broken", "performance": "Performance work",
    "risk:low": "Low blast radius, code may proceed without plan approval", "risk:high": "Needs human plan approval before code",
    "size:S": "Small", "size:M": "Medium", "size:L": "Large, split into child issues",
    "autonomy:dark": "Merge may be automatic (dark path, green CI)", "autonomy:lit": "Needs a human approval before merge",
    "needs-human": "Factory stopped, a human must act", "ona-user": "Reported by an end user", "epic": "Parent of child issues",
    "plan:approved": "Human approved the plan", "ci-fix:attempted": "One automatic CI fix already tried",
    "factory:accepted": "Author trusted by the factory",
    "spike": "Investigation only: findings are posted as a comment, no PR"
  }[.] // "";
  .labels | [.[][]] | .[] | [., color, desc] | @tsv' "$CONFIG" \
| while IFS=$'\t' read -r name color desc; do
    gh label create "$name" --repo "$TARGET_REPO" --color "$color" --description "$desc" --force >/dev/null
    log "label ok: $name"
  done
