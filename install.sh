#!/usr/bin/env bash
# ============================================================================
#  GOLD DESK — one-command installer
#
#  Install:   curl -fsSL https://raw.githubusercontent.com/taniask1988-commits/the-gold-desk/main/install.sh | bash
#  Launch:    gold-desk            (after restarting the terminal)
#
#  Non-interactive by design (safe under `curl | bash`). Overrides:
#    GOLD_DESK_REPO   clone URL      (default: the GitHub repo)
#    GOLD_DESK_DIR    install path   (default: ~/gold-desk)
#    GOLD_DESK_BIN    launcher dir   (default: ~/.local/bin)
#    GOLD_DESK_SKIP_WEB=1   skip web deck dependency install
#    GOLD_DESK_SKIP_TESTS=1 skip the 92-test self-verification
# ============================================================================
set -euo pipefail

REPO_URL="${GOLD_DESK_REPO:-https://github.com/taniask1988-commits/the-gold-desk.git}"
INSTALL_DIR="${GOLD_DESK_DIR:-$HOME/gold-desk}"
BIN_DIR="${GOLD_DESK_BIN:-$HOME/.local/bin}"

GOLD='\033[38;5;214m'; BOLD='\033[1m'; DIM='\033[2m'; GREEN='\033[38;5;71m'; RED='\033[38;5;203m'; OFF='\033[0m'
say()  { printf "${GOLD}◆${OFF} %s\n" "$*"; }
ok()   { printf "  ${GREEN}✓${OFF} %s\n" "$*"; }
warn() { printf "  ${RED}!${OFF} %s\n" "$*"; }
die()  { printf "${RED}✗ %s${OFF}\n" "$*" >&2; exit 1; }

printf "${GOLD}${BOLD}\n"
cat << 'BANNER'
   ┌─────────────────────────────────────────────┐
   │                                             │
   │      Au   G O L D   D E S K                 │
   │      XAUUSD H1 · fail-closed · journaled    │
   │                                             │
   └─────────────────────────────────────────────┘
BANNER
printf "${OFF}${DIM}  silent decision harness · deterministic pipeline · zero-LLM live loop${OFF}\n\n"

# ---------------------------------------------------------------- 1. checks
say "Checking prerequisites…"
command -v git >/dev/null 2>&1 || die "git not found. Install it first: https://git-scm.com/downloads"
ok "git $(git --version | awk '{print $3}')"

PY=""
for c in python3.12 python3.13 python3 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$(command -v "$c")"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.11+ not found.
     macOS:   xcode-select --install && brew install python@3.12
     Debian:  sudo apt install python3 python3-venv python3-pip
     Windows: use WSL or install from https://python.org"
ok "python $($PY --version | awk '{print $2}')"

# ---------------------------------------------------------------- 2. clone
say "Fetching the desk → $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  say "Existing install found — updating instead of cloning."
  git -C "$INSTALL_DIR" pull --ff-only >/dev/null 2>&1 || warn "git pull failed (local changes?) — keeping current files"
else
  mkdir -p "$INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || git clone "$REPO_URL" "$INSTALL_DIR"
fi
ok "code at $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"

# ---------------------------------------------------------------- 3. venv
say "Creating isolated Python environment…"
VENV_PY="$INSTALL_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  # path 1: standard venv (macOS, most Linux)
  "$PY" -m venv "$INSTALL_DIR/.venv" 2>/dev/null || true
  # path 2: venv --without-pip + system pip targeting it (Debian without python3-venv)
  if [ ! -x "$VENV_PY" ] && "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m venv --without-pip "$INSTALL_DIR/.venv" 2>/dev/null || true
    if [ -x "$VENV_PY" ]; then
      "$PY" -m pip --python "$VENV_PY" install --quiet PyYAML pytest >/dev/null 2>&1 || true
    fi
  fi
  # path 3: uv (fast, no ensurepip needed)
  if [ ! -x "$VENV_PY" ] && command -v uv >/dev/null 2>&1; then
    uv venv "$INSTALL_DIR/.venv" >/dev/null 2>&1 || true
    [ -x "$VENV_PY" ] && uv pip install --python "$VENV_PY" --quiet PyYAML pytest >/dev/null 2>&1 || true
  fi
  [ -x "$VENV_PY" ] || die "could not create a virtual environment.
     Debian/Ubuntu: sudo apt install python3-venv
     Fedora:        sudo dnf install python3-devel
     or install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
# ensure PyYAML no matter which path created the venv
"$VENV_PY" -c "import yaml" >/dev/null 2>&1 || \
  "$VENV_PY" -m pip install --quiet PyYAML >/dev/null 2>&1 || \
  die "failed to install PyYAML into the venv (network?)"
"$VENV_PY" -c "import pytest" >/dev/null 2>&1 || \
  "$VENV_PY" -m pip install --quiet pytest >/dev/null 2>&1 || true
ok "virtualenv ready (PyYAML installed)"

# ---------------------------------------------------------------- 4. tests
if [ "${GOLD_DESK_SKIP_TESTS:-0}" != "1" ]; then
  say "Self-verification — running the frozen test matrix…"
  (cd "$INSTALL_DIR" && "$VENV_PY" -m pytest tests/ -q 2>/dev/null | tail -1 | sed 's/^/  /') \
    || die "tests failed — the desk refuses to install unverified code"
  ok "test matrix green"
fi

# ---------------------------------------------------------------- 5. journal
say "Generating the 90-day demo journal…"
if [ ! -d "$INSTALL_DIR/data/events" ]; then
  (cd "$INSTALL_DIR" && PYTHONPATH="$INSTALL_DIR/src" "$VENV_PY" -m gold_desk.cli demo --days 90 --seed 42 >/dev/null 2>&1) \
    || warn "demo journal generation failed — run: gold-desk demo"
  ok "journal at $INSTALL_DIR/data"
else
  ok "journal already present (kept)"
fi

# ---------------------------------------------------------------- 6. web deps
if [ "${GOLD_DESK_SKIP_WEB:-0}" != "1" ]; then
  if command -v bun >/dev/null 2>&1; then
    say "Installing web deck dependencies (bun)…"
    (cd "$INSTALL_DIR/web" && bun install --silent >/dev/null 2>&1) && ok "web deck ready" || warn "bun install failed — run: gold-desk web (it will retry)"
  elif command -v npm >/dev/null 2>&1; then
    say "Installing web deck dependencies (npm)…"
    (cd "$INSTALL_DIR/web" && npm install --silent --no-fund --no-audit >/dev/null 2>&1) && ok "web deck ready" || warn "npm install failed — run: gold-desk web (it will retry)"
  else
    warn "node/bun not found — web deck skipped. The TUI still works: gold-desk tui"
  fi
fi

# ---------------------------------------------------------------- 7. launcher
say "Installing the gold-desk command…"
mkdir -p "$BIN_DIR"
sed "s|__GOLD_DESK_ROOT__|$INSTALL_DIR|g" "$INSTALL_DIR/bin/gold-desk" > "$BIN_DIR/gold-desk"
chmod +x "$BIN_DIR/gold-desk"
ok "launcher at $BIN_DIR/gold-desk"

# ---------------------------------------------------------------- 8. PATH
case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR already on PATH" ;;
  *)
    RC_FILE="$HOME/.bashrc"
    [ -n "${ZSH_VERSION:-}" ] && RC_FILE="$HOME/.zshrc"
    MARKER="# Added by gold-desk installer"
    if ! grep -qs "$MARKER" "$RC_FILE" 2>/dev/null; then
      printf '\n%s\nexport PATH="%s:\$PATH"\n' "$MARKER" "$BIN_DIR" >> "$RC_FILE"
    fi
    ok "PATH configured in $RC_FILE"
    ;;
esac

# ---------------------------------------------------------------- done
printf "\n${GOLD}${BOLD}  The desk is installed.${OFF}\n\n"
printf "  ${BOLD}Restart your terminal${OFF} (so PATH loads), then:\n\n"
printf "    ${GOLD}gold-desk${OFF}        launch the web command deck\n"
printf "    ${GOLD}gold-desk tui${OFF}     launch the terminal UI\n"
printf "    ${GOLD}gold-desk doctor${OFF}  verify the installation\n"
printf "    ${GOLD}gold-desk help${OFF}    every command\n\n"
printf "${DIM}  Installed at: $INSTALL_DIR${OFF}\n\n"
