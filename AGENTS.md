# BlinkyMap — Codex Session Context

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
- **Split control/camera across devices works unmodified** (verified 2026-08-17).
  `pixel_on` / `capture_background` are broadcast to every client, and a
  `detection` is accepted from any of them, so a laptop can drive the UI while
  a phone supplies the camera. A camera-less client replies `no_detection`,
  which the server drops rather than queues — the same choice that fixed the
  two-tab race is what makes this safe. Tested with a client sending instant
  `no_detection` alongside one sending delayed `detection`: 6/6 counted from
  the camera client. Caveats: only ONE client may have a camera open (two would
  race, first-write-wins), and the phone must not sleep — mobile browsers
  suspend JS on screen lock, which looks identical to "pixel not visible".

## Deployment Environment

### Current test box — 192.168.25.111 ("FPP-Test")
Plugin is installed and verified here (2026-08-17).
- FPP 9.5.3 on Raspbian Bookworm, Pi Zero 2 W, K2-Pi0 cape, mode=player, multisync **off**
- Apache 2 + PHP 8.2-FPM; DocumentRoot `/opt/fpp/www`
- Plugin dir: `/home/fpp/media/plugins/blinkymap`
- Deps from apt: numpy 1.24.2, websockets 10.4, requests 2.28.1 (no pip on this image)
- **Pixel setup** — two configured strings, only the first is physically populated:
  | Port | Description | Pixels | FPP channels (1-based) | Populated |
  |------|-------------|--------|------------------------|-----------|
  | 0 | Test String 1 | 24 | 1–72 | **yes** |
  | 1 | Test String 2 | 100 | 1001–1300 | no |
  Channels 73–1000 map to nothing. A sweep spanning both strings lights 24
  pixels and then appears to stall — that is the gap, not a bug.
  Use Start Channel `1`, Pixel Count `24`.
- SSH: `ssh fpp@192.168.25.111` (key `~/.ssh/fpp_ed25519`, entry in `~/.ssh/config`)

### Older boxes (from earlier sessions, not currently in use)
- **FPP Master**: Debian 12 at 192.168.25.207 — 50 pixels, start channel 9004, multisync enabled
- **FPP Remote**: Raspberry Pi Zero (K2-Pi0) at 192.168.25.204

### Operations
- **Server restart**: `sudo pkill -f blinkymap_server` (www/index.php auto-restarts on next page load)
- **Logs**: `/tmp/blinkymap_server.log` on the box running the plugin

## FPP API Notes
Verified against FPP 9.5.3 on 2026-08-17.
- Light one pixel: `POST /api/command` with
  `{"command":"Test Start","multisyncCommand":true,"multisyncHosts":"","args":["100","RGB Single Color","<startCh>-<endCh>","#rrggbb"]}`
  The channel range is inclusive and absolute. Confirm it took with
  `GET /api/testmode` → `{"channelSet":"1-3",...,"enabled":1}`.
- Stop: `POST /api/command` with `{"command":"Test Stop","multisyncCommand":true,"args":[]}`
  (`GET /api/testmode` then returns `{"enabled":0}`).
- FPP channels are 1-indexed and absolute (not per-port). RGB pixels consume
  **3 channels each**, so with start channel S, pixel *i* occupies
  `S + i*3` through `S + i*3 + 2` — e.g. start 1 → pixel 0 = ch 1–3,
  pixel 1 = ch 4–6, pixel 23 = ch 70–72.

## Development Branch
Current working branch: `Codex/fpp-plugin-testing-b9pzvc`, then merges to `main`.

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
1. Open FPP UI → navigate to BlinkyMap (or go direct to `https://<fpp-ip>/plugin/blinkymap/`)
2. Setup tab: enter FPP IP, pixel count (50), start channel (9004), save & connect
3. Open camera, check green camera status bar
4. Scan tab: set angle/distance/height, start session — verify pixels counted > 0
5. After scan: check expandable session card shows per-pixel detail
6. Check confidence score and tip updates after each scan
7. Export tab: download .xmodel and import into xLights Layout
