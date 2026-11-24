#!/usr/bin/env bash
# Example: scripts/labels.sh --owner YOUR_ORG --repo example-game
set -euo pipefail

if [[ $# -ne 4 || "$1" != --owner || -z "$2" || "$3" != --repo || -z "$4" ]]; then
  echo "Usage: scripts/labels.sh --owner OWNER --repo REPO" >&2
  exit 2
fi

repository="$2/$4"
while IFS='|' read -r name color description; do
  gh label create "$name" --repo "$repository" --color "$color" --description "$description" --force
done <<'LABELS'
P0|b60205|Stop work and contact the repository owner
P1|d93f0b|Requires a decision from the repository owner
P2|fbca04|Milestone eligible for merge after review, with notification
P3|0e8a16|Routine change eligible for merge after review
needs-owner|b60205|Requires the repository owner to handle this pull request
needs-author|5319e7|Waiting for the pull request author
needs-arch|1d76db|Waiting for architecture review
needs-rebase|c5def5|Update the branch against its base before merging
good first issue|7057ff|Suggested first contribution
LABELS
