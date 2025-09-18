#!/usr/bin/env bash
set -euo pipefail

# Determine base directory (support Git Bash/WSL paths)
BASE_DIR="/home/strato-space"
if [ ! -d "$BASE_DIR" ]; then
  BASE_DIR="/d/home/strato-space"
fi
if [ ! -d "$BASE_DIR" ]; then
  BASE_DIR="$(pwd)"
fi
cd "$BASE_DIR"

echo "Working in: $PWD"

repo() {
  local url="$1"
  local dir
  if [ "${2-}" != "" ]; then
    dir="$2"
  else
    # Derive directory from URL's last path component, strip optional .git
    local last_component
    last_component="${url##*/}"
    dir="${last_component%.git}"
  fi

  if [ -d "$dir/.git" ]; then
    echo "Updating $dir..."
    git -C "$dir" pull --ff-only
  elif [ -d "$dir" ]; then
    echo "Directory $dir exists but is not a git repository. Skipping to avoid overwriting."
  else
    echo "Cloning $url into $dir..."
    git clone "$url" "$dir"
  fi
}

# Backward-compatible alias
ensure_repo() { repo "$@"; }

# Ensure core repositories
repo https://github.com/strato-space/call
repo https://github.com/strato-space/agent
repo https://github.com/strato-space/prompt
repo https://github.com/strato-space/server
repo https://github.com/strato-space/rms
repo https://github.com/strato-space/voice

# Optional repositories
# repo https://github.com/strato-space/telegram-mcp
# repo https://github.com/strato-space/mcp-google-sheets
# repo https://github.com/strato-space/mcp-telegram
# repo https://github.com/strato-space/ai
