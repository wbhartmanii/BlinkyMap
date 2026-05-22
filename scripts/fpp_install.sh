#!/bin/bash
# BlinkyMap FPP Plugin installer
# Called by FPP Plugin Manager after cloning the repo.

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # scripts/ -> plugin root
WWW_DIR="$PLUGIN_DIR/www/blinkymap"

echo "BlinkyMap: installing Python dependencies..."
# Use python3 -m pip (pip3 may not be in PATH on all FPP installs)
if command -v pip3 &>/dev/null; then
    pip3 install --quiet --upgrade numpy websockets requests 2>&1 | tail -5
elif command -v python3 &>/dev/null; then
    python3 -m pip install --quiet --upgrade numpy websockets requests 2>&1 | tail -5
else
    echo "WARNING: pip3/python3 not found, skipping Python deps"
fi

echo "BlinkyMap: downloading Three.js..."
mkdir -p "$WWW_DIR/vendor"

# Three.js r160 (ESM build used via importmap)
THREE_VERSION="0.160.0"
THREE_BASE="https://cdn.jsdelivr.net/npm/three@${THREE_VERSION}"

curl -fsSL "${THREE_BASE}/build/three.module.min.js" \
     -o "$WWW_DIR/vendor/three.module.min.js"

curl -fsSL "${THREE_BASE}/examples/jsm/controls/OrbitControls.js" \
     -o "$WWW_DIR/vendor/OrbitControls.js"

# Patch OrbitControls import path to point at our local three.module
sed -i "s|from 'three'|from './three.module.min.js'|g" \
    "$WWW_DIR/vendor/OrbitControls.js"

echo "BlinkyMap: install complete."
