#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./tools/repos.sh [--pull] [--pip] [--mcp] [--all] [--codex]

Flags:
  --pull       Clone or update repositories listed in this script.
  --pip        Ensure .venv exists and install Python dependencies from requirements files.
  --mcp        Install uv and JavaScript MCP servers (filesystem, sequential-thinking).
  --all        Run all workflows: --pull, --pip, and --mcp.
  --codex      Run all workflows but skip cloning/updating the call repo.
  -h, --help   Show this help message.
EOF
}

log_section() {
  printf '\n==> %s\n' "$1"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

detect_python_binary() {
  if command_exists python3; then
    echo "python3"
    return 0
  fi
  if command_exists python; then
    echo "python"
    return 0
  fi
  return 1
}

ensure_venv() {
  if [ -d ".venv" ]; then
    return 0
  fi

  local python_bin
  if ! python_bin="$(detect_python_binary)"; then
    echo "Python interpreter not found. Please install Python 3 before continuing." >&2
    return 1
  fi

  log_section "Creating Python virtual environment (.venv) with ${python_bin}"
  "${python_bin}" -m venv .venv
}

activate_venv() {
  if [ -n "${VIRTUAL_ENV-}" ]; then
    echo "Using active virtual environment at: ${VIRTUAL_ENV}"
    return 0
  fi

  if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . ".venv/bin/activate"
    echo "Activated virtual environment using .venv/bin/activate"
    return 0
  fi

  if [ -f ".venv/Scripts/activate" ]; then
    # shellcheck disable=SC1091
    . ".venv/Scripts/activate"
    echo "Activated virtual environment using .venv/Scripts/activate"
    return 0
  fi

  echo "Unable to auto-activate .venv. Activate manually (e.g., 'source .venv/bin/activate' or '.\\.venv\\Scripts\\Activate.ps1')."
  return 1
}

venv_python_path() {
  if [ -n "${VIRTUAL_ENV-}" ]; then
    if [ -x "${VIRTUAL_ENV}/bin/python" ]; then
      echo "${VIRTUAL_ENV}/bin/python"
      return 0
    fi
    if [ -x "${VIRTUAL_ENV}/Scripts/python.exe" ]; then
      echo "${VIRTUAL_ENV}/Scripts/python.exe"
      return 0
    fi
    if [ -x "${VIRTUAL_ENV}/Scripts/python" ]; then
      echo "${VIRTUAL_ENV}/Scripts/python"
      return 0
    fi
  fi

  if [ -x ".venv/bin/python" ]; then
    echo ".venv/bin/python"
    return 0
  fi
  if [ -x ".venv/Scripts/python.exe" ]; then
    echo ".venv/Scripts/python.exe"
    return 0
  fi
  if [ -x ".venv/Scripts/python" ]; then
    echo ".venv/Scripts/python"
    return 0
  fi

  return 1
}

install_python_requirements() {
  local venv_py="$1"
  shift
  local requirement
  for requirement in "$@"; do
    if [ -f "$requirement" ]; then
      log_section "Installing Python dependencies from $requirement"
      "$venv_py" -m pip install -r "$requirement"
    else
      echo "Skipping missing requirements file: $requirement"
    fi
  done
}

setup_pip() {
  ensure_venv

  if activate_venv; then
    :
  else
    echo "Continuing without interactive activation; using virtualenv python directly."
  fi

  local venv_python
  if ! venv_python="$(venv_python_path)"; then
    echo "Unable to locate Python executable inside .venv. Please verify the environment." >&2
    return 1
  fi

  log_section "Upgrading pip inside the virtual environment"
  "$venv_python" -m pip install --upgrade pip

  install_python_requirements "$venv_python" \
    "call/requirements.txt" \
    "voice/requirements.txt" \
    "server/mcp/requirements.txt"
}

run_pip_workflow() {
  log_section "Running --pip workflow"
  if setup_pip; then
    echo "Python dependencies installed successfully."
  else
    echo "Python dependency setup encountered issues; review the logs above." >&2
  fi
}

install_uv_linux() {
  if command_exists uv; then
    return 0
  fi

  if command_exists snap; then
    log_section "Installing astral-uv via snap (requires sudo)"
    if sudo snap install astral-uv --classic; then
      if command_exists uv; then
        return 0
      fi
    else
      echo "snap installation failed. Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
    fi
  else
    echo "'snap' is not available. Install uv manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
  fi

  return 1
}

install_uv_windows() {
  if command_exists uv; then
    return 0
  fi

  if command_exists powershell.exe; then
    log_section "Installing astral-uv via PowerShell bootstrapper"
    local ps_command
    ps_command="Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass; irm https://astral.sh/uv/install.ps1 | iex"
    if powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ps_command"; then
      if command_exists uv; then
        return 0
      fi
    else
      echo "PowerShell installer for uv failed. You may need to rerun manually." >&2
    fi
  else
    echo "PowerShell not found; skipping PowerShell-based uv installation." >&2
  fi

  if command_exists winget; then
    log_section "Ensuring astral-uv via winget"
    if winget install --id astral-sh.uv -e --source winget; then
      if command_exists uv; then
        return 0
      fi
    else
      echo "winget installation of uv did not complete successfully. Try running manually." >&2
    fi
  else
    echo "'winget' command not found. Install uv manually if needed." >&2
  fi

  return 1
}

ensure_uv() {
  if command_exists uv; then
    echo "uv is already installed."
    return 0
  fi

  local uname_s
  uname_s="$(uname -s)"
  case "$uname_s" in
    Linux*) install_uv_linux ;;
    MINGW*|MSYS*|CYGWIN*) install_uv_windows ;;
    *)
      echo "Unsupported platform (${uname_s}). Install uv manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
      return 1
      ;;
  esac

  if command_exists uv; then
    echo "uv installation confirmed."
    return 0
  fi

  echo "uv was not detected after installation attempts. Please install it manually." >&2
  return 1
}

install_mcp_node_servers() {
  local -a npm_cmd=()

  if command_exists npm; then
    npm_cmd=(npm)
  else
    case "$(uname -s)" in
      MINGW*|MSYS*|CYGWIN*)
        if command_exists nvm && [ -n "${NVM_SYMLINK-}" ]; then
          local npm_cmd_path
          npm_cmd_path="${NVM_SYMLINK}\\npm.cmd"
          if [ -f "$npm_cmd_path" ]; then
            npm_cmd=(cmd.exe /c "$npm_cmd_path")
          fi
        fi
        ;;
    esac
  fi

  if [ ${#npm_cmd[@]} -gt 0 ]; then
    log_section "Installing MCP JavaScript servers via npm"
    "${npm_cmd[@]}" install @modelcontextprotocol/server-sequential-thinking @modelcontextprotocol/server-filesystem
    return 0
  fi

  if command_exists nvm; then
    echo "Detected nvm but npm (Node.js) is not available in the current shell."
    echo "Install and activate a Node.js version (e.g., 'nvm install --lts' followed by 'nvm use --lts'), then rerun with --mcp."
  else
    echo "npm was not detected. Install Node.js or nvm to proceed."
    echo "For nvm on Windows: https://github.com/coreybutler/nvm-windows/releases/download/1.2.2/nvm-setup.exe"
    echo "For nvm on Linux/macOS: https://github.com/nvm-sh/nvm"
  fi
}

setup_mcp() {
  if ! ensure_uv; then
    echo "Proceeding without uv; install it manually if required." >&2
  fi
  install_mcp_node_servers
}

DO_PULL=false
DO_PIP=false
DO_MCP=false
DO_CODEX=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull)
      DO_PULL=true
      ;;
    --pip)
      DO_PIP=true
      ;;
    --mcp)
      DO_MCP=true
      ;;
    --all)
      DO_PULL=true
      DO_PIP=true
      DO_MCP=true
      ;;
    --codex)
      DO_CODEX=true
      DO_PULL=true
      DO_PIP=true
      DO_MCP=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

# Determine base directory relative to this script (support symlinks)
# Resolve the entry point, even if the script is sourced via a symlink.
SOURCE="${BASH_SOURCE[0]}"
# Follow symlinks repeatedly until we land on a real file.
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
# Canonical path to the directory containing the script file.
if ! SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"; then
  echo "Unable to determine script directory." >&2
  exit 1
fi
# Two levels up from call/tools -> repo root (call sits one level above).
if ! BASE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"; then
  echo "Unable to determine base directory from script path." >&2
  exit 1
fi
cd "$BASE_DIR"

# 1) Load variables from .env (export all)
# Warning: this executes lines as shell code — .env must be trusted.
set -a
. <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' call/.env)
set +a

echo "Working in: $PWD"

if [ "$DO_PIP" = true ]; then
  run_pip_workflow
fi

# If running under a Windows-like shell (Git Bash/MSYS/Cygwin), set consistent EOL in the main workspace.
# This matches the user's desired config:
#   git -C "<path>" config core.autocrlf false
#   git -C "<path>" config core.eol lf
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    WIN_ROOT="$BASE_DIR"
    if command_exists cygpath; then
      # Convert POSIX path to Windows absolute path for Git commands.
      WIN_ROOT="$(cygpath -am "$BASE_DIR")"
    fi
    if [ -d "$WIN_ROOT/.git" ]; then
      git -C "$WIN_ROOT" config core.autocrlf false || true
      git -C "$WIN_ROOT" config core.eol lf || true
      echo "Applied Git EOL config to $WIN_ROOT (autocrlf=false, eol=lf)"
    fi
    ;;
  *) ;;
esac

repo() {
  local url="$1"
  local dir
  local clone_url="$url"
  if [ "${2-}" != "" ]; then
    dir="$2"
  else
    # Derive directory from URL's last path component, strip optional .git
    local last_component
    last_component="${url##*/}"
    dir="${last_component%.git}"
  fi

  if [ -n "${GITHUB_TOKEN_PROMPT:-}" ]; then
    clone_url="https://x-access-token:${GITHUB_TOKEN_PROMPT}@github.com/${url#https://github.com/}"
  fi

  if [ -d "$dir/.git" ]; then
    echo "Updating $dir..."
    git -C "$dir" remote get-url origin >/dev/null 2>&1 || git -C "$dir" remote add origin "$clone_url"
    if [ -n "${GITHUB_TOKEN_PROMPT:-}" ]; then
      git -C "$dir" remote set-url origin "$clone_url"
    fi
    git -C "$dir" pull --ff-only
  elif [ -d "$dir" ]; then
    echo "Directory $dir exists but is not a git repository. Skipping to avoid overwriting."
  else
    echo "Cloning $clone_url into $dir..."
    git clone "$clone_url" "$dir"
  fi
}

if [ "$DO_PULL" = true ]; then
  log_section "Running --pull workflow"
  if [ "$DO_CODEX" != true ]; then
    repo https://github.com/strato-space/call
  else
    echo "Skipping call repository in --codex mode."
  fi
  repo https://github.com/strato-space/agent
  repo https://github.com/strato-space/prompt
  repo https://github.com/strato-space/server
  repo https://github.com/strato-space/rms
  repo https://github.com/strato-space/voice
  # repo https://github.com/chigwell/telegram-mcp
  repo https://github.com/strato-space/telegram-mcp
  repo https://github.com/strato-space/telegram-mcp-ro
  repo https://github.com/xing5/mcp-google-sheets
  
  uv --directory voice sync 
  uv --directory mcp-google-sheets sync 
  uv --directory telegram-mcp sync 
  uv --directory telegram-mcp-ro sync 

  # Optional repositories
  # repo https://github.com/strato-space/telegram-mcp
  # repo https://github.com/strato-space/mcp-google-sheets
  # repo https://github.com/strato-space/mcp-telegram
  # repo https://github.com/strato-space/ai
  if [ -d "call" ]; then
    CACHE_ROOT="${BASE_DIR}/.cache/call"
    if [ ! -d "$CACHE_ROOT" ]; then
      mkdir -p "$CACHE_ROOT"
      echo "Created cache directory: $CACHE_ROOT"
    fi
  fi

  python -m call.cli.main reload

  if [ "$DO_CODEX" != true ] && command_exists systemctl; then
    log_section "Copy services config"
    cp -a server/mcp/etc/. /etc/
    systemctl daemon-reload
    log_section "Restarting call services"
    sudo systemctl restart actions@call mcp@call actions@voice mcp@voice mcp@fs mcp@seq nginx

  fi
fi
 
if [ "$DO_MCP" = true ]; then
  log_section "Running --mcp workflow"
  if setup_mcp; then
    echo "MCP tooling steps completed."
  else
    echo "MCP tooling encountered issues; review the logs above." >&2
  fi
  if ! command_exists nvm; then
    echo "If nvm is not installed on Windows, download the installer from:"
    echo "  https://github.com/coreybutler/nvm-windows/releases/download/1.2.2/nvm-setup.exe"
    echo "After installing nvm, install Node.js (e.g., 'nvm install --lts' && 'nvm use --lts') and rerun with --mcp as needed."
  fi
fi
