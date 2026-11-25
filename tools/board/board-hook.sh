#!/usr/bin/env bash
# Refresh trusted tools and board data; unavailable boards never block a session.
set -uo pipefail
mode="${1:-start}"
shift || true
project="${CLAUDE_PROJECT_DIR:-$PWD}"
agent="${BOARD_AGENT:-claude}"
config="$project/.soloforge/board.json"
if [[ -f "$config" ]]; then
  configured_owner=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["owner"])' "$config" 2>/dev/null) || configured_owner=""
  configured_url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["url"])' "$config" 2>/dev/null) || configured_url=""
fi
export SOLOFORGE_OWNER="${SOLOFORGE_OWNER:-${configured_owner:-}}"
url="${SOLOFORGE_URL:-${configured_url:-}}"
cache_key=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])' "$url")
cache="${BOARD_CACHE:-$HOME/.cache/soloforge/$cache_key}"
export BOARD_DIR="${BOARD_DIR:-$cache/board}"

matching_origin() {
  [[ -n "$url" && "$(git -C "$1" remote get-url origin 2>/dev/null)" == "$url" ]]
}

sync_branch() {
  local destination="$1" branch="$2"
  if [[ -e "$destination/.git" ]]; then
    matching_origin "$destination" || return 1
    git -C "$destination" pull -q --rebase origin "$branch"
  elif [[ ! -e "$destination" && -n "$url" ]]; then
    mkdir -p -- "$(dirname -- "$destination")"
    git clone -q --depth 20 --branch "$branch" -- "$url" "$destination"
  else
    return 1
  fi
}

tools="${SOLOFORGE_DIR:-$cache/main}"
if [[ "$mode" == start ]]; then
  if [[ -z "${SOLOFORGE_DIR:-}" ]]; then sync_branch "$tools" main >/dev/null 2>&1 || true; fi
  sync_branch "$BOARD_DIR" board >/dev/null 2>&1 || true
fi
available=true
matching_origin "$BOARD_DIR" || available=false
if [[ -z "${SOLOFORGE_DIR:-}" ]]; then matching_origin "$tools" || available=false; fi
if [[ "$available" != true || -z "$SOLOFORGE_OWNER" || ! -f "$tools/tools/board/board.py" || ! -e "$BOARD_DIR/.git" ]]; then
  if [[ "$mode" == start ]]; then
    message="Board unavailable. Record a handoff in the PR description and restore the configured board connection."
    if [[ "$agent" == claude ]]; then
      printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$message"
    else
      printf '%s\n' "$message"
    fi
  fi
  exit 0
fi
cd -- "$project" || exit 0
exec python3 "$tools/tools/board/board.py" "hook-$mode" --agent "$agent" "$@"
