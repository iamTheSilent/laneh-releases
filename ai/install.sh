#!/bin/sh
# Connects Claude (Claude Code and the Claude app) to Laneh Team on Mac/Linux.
#   curl -fsSL https://raw.githubusercontent.com/iamTheSilent/laneh-releases/main/ai/install.sh | sh
set -e
BASE=https://raw.githubusercontent.com/iamTheSilent/laneh-releases/main/ai
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Laneh needs Python 3.10+: https://www.python.org/downloads/" >&2
  exit 1
fi
echo "Installing the MCP library..."
"$PY" -m pip install --user --upgrade --quiet 'mcp>=1.2' 2>/dev/null ||
  "$PY" -m pip install --user --upgrade --quiet --break-system-packages 'mcp>=1.2'
mkdir -p "$HOME/.laneh"
curl -fsSL "$BASE/laneh_mcp.py" -o "$HOME/.laneh/laneh_mcp.py"
"$PY" "$HOME/.laneh/laneh_mcp.py" --install
