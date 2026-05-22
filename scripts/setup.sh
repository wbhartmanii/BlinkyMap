#!/bin/bash
# BlinkyMap one-shot install + diagnostics for FPP
# Usage: curl -fsSL https://raw.githubusercontent.com/wbhartmanii/BlinkyMap/main/scripts/setup.sh | bash

set -uo pipefail
PLUGIN_DIR="/home/fpp/media/plugins/blinkymap"
REPO="https://github.com/wbhartmanii/BlinkyMap.git"

hr() { echo "----------------------------------------"; }

hr
echo "BlinkyMap setup — $(date)"
hr

# ── FPP info ──────────────────────────────────────────────────────────────────
echo "FPP version: $(cat /etc/fpp_version 2>/dev/null || echo unknown)"
echo "OS: $(cat /etc/os-release 2>/dev/null | grep PRETTY | cut -d= -f2 | tr -d '"' || uname -a)"
echo "User: $(whoami)"
echo "Plugin dir: $PLUGIN_DIR"
hr

# ── Clone or update ───────────────────────────────────────────────────────────
if [ -d "$PLUGIN_DIR/.git" ]; then
  echo "Plugin already cloned — pulling latest..."
  git -C "$PLUGIN_DIR" pull
else
  echo "Cloning BlinkyMap..."
  rm -rf "$PLUGIN_DIR"
  git clone "$REPO" "$PLUGIN_DIR"
fi
hr

# ── Install deps + vendor Three.js ───────────────────────────────────────────
echo "Running fpp_install.sh..."
bash "$PLUGIN_DIR/scripts/fpp_install.sh"
hr

# ── Verify file layout ────────────────────────────────────────────────────────
echo "Plugin file layout:"
find "$PLUGIN_DIR" -not -path '*/.git/*' | sort | head -40
hr

# ── Check menu.inc ────────────────────────────────────────────────────────────
echo "menu.inc contents:"
cat "$PLUGIN_DIR/menu.inc"
hr

# ── Check FPP web alias ───────────────────────────────────────────────────────
echo "FPP web server config snippets (plugin alias):"
grep -r 'plugin' /etc/lighttpd/ 2>/dev/null | head -10 || \
grep -r 'plugin' /etc/nginx/ 2>/dev/null | head -10 || \
echo "(no web config found at usual paths)"
hr

# ── Restart fppd ─────────────────────────────────────────────────────────────
echo "Restarting fppd..."
sudo systemctl restart fppd 2>&1 && echo "fppd restarted OK" || echo "fppd restart failed (may need sudo)"
sleep 3

# ── fppd plugin log ───────────────────────────────────────────────────────────
echo "Recent fppd log (plugin lines):"
sudo journalctl -u fppd --no-pager -n 50 2>/dev/null | grep -i 'plugin\|blinky\|menu\|error' || echo "(no matching log lines)"
hr
echo "Done. Paste the output above back to Claude."
