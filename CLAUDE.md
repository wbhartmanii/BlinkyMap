# BlinkyMap — Claude Session Context

## Project Overview
BlinkyMap is an FPP (Falcon Player) plugin that automatically builds a 3D xLights model for pixel-wrapped props (trees, arches, etc.) by triangulating pixel positions from a phone camera moved to multiple scan positions.

## Architecture

### Components
- **`blinkymap_server.py`** — Python asyncio WebSocket server (port 8765). Handles FPP API calls, scan orchestration, pixel detection sync, and 3D model triangulation.
- **`www/blinkymap/app.js`** — Single-page app JavaScript (ES modules, Three.js 3D viewer, WebSocket client).
- **`www/blinkymap/index.html`** — SPA shell with 4 tabs: Setup, Scan, 3D Model, Export.
- **`www/blinkymap/style.css`** — Dark mobile-first theme.
- **`www/index.php`** — FPP entry point. Starts Python server if needed, forces HTTPS, redirects to SPA.
- **`scripts/fpp_install.sh`** — FPP Plugin Manager installer. Installs Python deps (numpy, websockets, requests), downloads Three.js 0.160.0, configures Apache alias + WebSocket proxy, generates self-signed SSL cert.
- **`menu.inc`** — FPP navigation menu entry (uses `plugin.php?plugin=` router).
- **`plugin.php`** — FPP plugin.php compatibility shim.
- **`pluginInfo.json`** — FPP plugin metadata; `branch: "main"` for production.

### Key Design Decisions
- **asyncio.Queue for detection sync** (not asyncio.Event — had a clear-race bug). The scan loop calls `asyncio.wait_for(queue.get(), timeout=0.5)`. `no_detection` is NOT queued; the 0.5s timeout serves as the "not detected" signal. This prevents two-browser-tab races where a stale tab's fast `no_detection` beat the active tab's camera-processing `detection`.
- **JS closure capture** in `pixel_on` handler uses `msg.index` (not `currentPixelIdx` shared state) to prevent race between async detection and `pixel_off` handler.
- **WebSocket proxy** through Apache at `/blinkymap-ws → ws://127.0.0.1:8765` satisfies FPP's same-origin CSP.
- **HTTPS required** for `getUserMedia` (camera) in all modern browsers. The install script generates a self-signed cert; users accept the browser warning once.
- **Asset cache busting** via `?v=N` query strings on CSS/JS/HTML. Increment `v=` when deploying to FPP (FPP caches aggressively).

## Deployment Environment

### Test platform (primary) — `FPP-Test`
- **Host**: Raspberry Pi Zero 2 W + Kulp K2-Pi0 hat, battery powered, at 192.168.25.111
- **Role**: standalone FPP instance — it is its own master, no multisync remote involved
- **Pixel setup**: **start channel 1** — standalone, so the K2-Pi0's own outputs start at
  the bottom of the channel range. This is also the `cfg-start` default in index.html,
  so the Setup tab needs no change. (The old 9004 was specific to the Debian master's
  channel map; it does not apply here.)
  Verified: at start channel 1, `FPPOutput.pixel_on` emits pixel 0 → `1-3`,
  pixel 1 → `4-6`, pixel N → `(1+3N)-(3+3N)`. Clean RGB triplets, no off-by-one.
- **Scan delay**: the Setup tab's delay field defaults to 0.15s, which was tuned to let
  multisync propagate to a remote. Standalone has no propagation hop, so this can go
  lower (~0.05–0.10s) for noticeably faster scans if detection stays reliable.

### Legacy two-box rig (kept for multisync regression testing)
- **FPP Master**: Debian 12 at 192.168.25.207
- **FPP Remote**: Raspberry Pi Zero (K2-Pi0) at 192.168.25.204
- **Pixel setup**: 50 pixels, port 1, starting channel 9004, FPP multisync enabled

### Operating notes (any host)
- **Server restart**: `sudo pkill -f blinkymap_server` (www/index.php auto-restarts on next page load)
- **Logs**: `/tmp/blinkymap_server.log` on whichever FPP box serves the plugin
- The plugin itself is host-agnostic — the FPP IP is entered in the Setup tab and
  `FPPOutput`/`E131Output` are chosen by auto-probing `/api/fppd/status`. No code
  change is needed to move between test rigs.

## FPP API Notes
- Light individual pixels: `POST /api/command` with `{"command":"Test Start","multisyncCommand":true,"args":["Pixel","1","<startCh>","<pixelIdx>",...]}`
- Stop: `POST /api/command` with `{"command":"Test Stop","multisyncCommand":true,"args":[]}`
- FPP channels are 1-indexed and absolute (not per-port). Starting channel 9004 means pixel 0 = channel 9004, pixel 1 = channel 9005 (for RGB: channels 9004-9006 for pixel 0).
- `multisyncCommand: true` is safe on a standalone instance — FPP applies the test
  locally and broadcasts to zero remotes. No need to special-case single-box rigs.

## Development Branch
Current working branch: `claude/fpp-test-platform-setup-w1kk02`, then merges to `main`.
(Previous branch, now merged: `claude/blinkymap-3d-modeling-J34d4`.)

## Known Issues / GitHub Issues
See https://github.com/wbhartmanii/BlinkyMap/issues for the current list.
Filed issues cover:
- Unit label line breaks (Distance from center `\nft\n`)
- Confidence tip not updating after 2nd scan
- Per-pixel found/not-found status during scan
- Pixel-by-pixel live view during scan
- 3D model tab contextual tips
- 2D-only mapping mode option
- m/ft toggle placement (currently in header, feels disconnected)
- Extrapolate positions for unseen pixels from neighbors
- 3D model updates incrementally, doesn't wait for 100% detection

## Testing Checklist
0. Confirm the string lights at all first: FPP UI → **Status/Control → Test**, pick the
   string's channel range, RGB Single Color. If nothing lights here, it's wiring or
   channel config, not BlinkyMap. (Cost us a session once — it was a bad pigtail.)
1. Open FPP UI → navigate to BlinkyMap (or go direct to `https://<fpp-ip>/plugin/blinkymap/`)
2. Setup tab: enter FPP IP and pixel count; start channel is 1 on FPP-Test (the
   field's default) and 9004 on the legacy master. Save & connect.
3. Open camera, check green camera status bar
4. Scan tab: set angle/distance/height, start session — verify pixels counted > 0
5. After scan: check expandable session card shows per-pixel detail
6. Check confidence score and tip updates after each scan
7. Export tab: download .xmodel and import into xLights Layout
