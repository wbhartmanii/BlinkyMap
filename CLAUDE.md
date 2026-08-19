# BlinkyMap — Claude Session Context

## Project Overview
BlinkyMap is an FPP (Falcon Player) plugin that automatically builds a 3D xLights model for pixel-wrapped props (trees, arches, etc.) by triangulating pixel positions from a phone camera moved to multiple scan positions.

## Architecture

### Components
- **`blinkymap_server.py`** — Python asyncio WebSocket server (port 8765). FPP
  output, scan orchestration, triangulation, export. Also owns the base-3
  coding used by structured-light scans.
- **`www/blinkymap/index.html`** — role chooser. Remembers the pick in
  localStorage; `?pick=1` forces it back.
- **`www/blinkymap/control.{html,js}`** — laptop UI: controller setup, session
  list, 3D model, export. No camera code at all.
- **`www/blinkymap/sensor.{html,js}`** — phone UI: camera, compass, and it
  starts scans (the operator is standing at the position). Single fixed screen,
  never scrolls.
- **`www/blinkymap/coded.js`** — structured-light detection: classifies each
  frame by dominant colour and resolves every pixel in one pass.
- **`www/blinkymap/camera.js`** — getUserMedia, multi-frame baseline, and the
  legacy one-pixel-at-a-time detector.
- **`www/blinkymap/compass.js`** — device heading, normalised across iOS/Android.
- **`www/blinkymap/tilt.js`** — camera depression below horizontal, from the
  accelerometer. Replaces both typed heights; see below.
- **`www/blinkymap/viewer3d.js`** — Three.js viewer; auto-fits to the model.
- **`www/index.php`** — FPP entry point. Starts the Python server, forces HTTPS,
  redirects to the role chooser.
- **`scripts/fpp_install.sh`** — installer: deps, Three.js, Apache alias,
  WebSocket proxy, self-signed cert, and `no-store` on the plugin's HTML.
- **`menu.inc`**, **`plugin.php`**, **`pluginInfo.json`** — FPP plugin wiring.

### How scanning works (structured light)
This is the important one. Rather than lighting one pixel per frame, **every
frame lights every pixel**, each coloured by one digit of its base-3 index
(0 red, 1 green, 2 blue). A pixel is located by finding the image region
carrying its colour in *every* frame. `log3(n)+2` frames instead of `n` — 5 for
24 pixels, 8 for 500. Adopted from xLights' `GenerateCustomModelDialog`; see
`docs/xlights-detection-notes.md`.

Speed is the least of it:
- A reflection must match the pixel's colour in **every** frame to survive
  (1 in 243 at five frames). The old one-pixel detector had no such defence and
  reflections regularly won, producing confident wrong positions.
- An occluded pixel leaves no region carrying its code, so **"not visible" is a
  geometric fact**, not a tuned threshold. Before this, every scan reported
  24/24 on a pile where pixels were plainly buried.
- Every frame lights the same number of LEDs, so scene brightness is constant
  and the camera never re-exposes between frames.

**Driving per-pixel colours** needs no E1.31 bridge. FPP's `"Custom Chase"`
takes an arbitrary hex `colorPattern`, and `TestPatternRGBChase::SetupTest`
lays it out *spatially across the string*, so a pattern holding exactly one RGB
triplet per pixel gives each its own colour. `cycleMS` is set to 600000 so the
chase cannot rotate mid-capture. Verified on real hardware.

Resolution is **one pass per frame regardless of pixel count**. Building a mask
per pixel and intersecting would be pixels x frames x imageArea (~110M ops for
24 pixels at 720x1280); instead each image pixel's per-frame colour classes fold
into a single base-3 code with centroids accumulated by code.

### Key Design Decisions
- **Roles are negotiated, not guessed.** Clients send
  `{type:"hello", role:"control"|"sensor"}`; the server **only accepts
  detections from a sensor**. A stray webcam on the laptop cannot race the
  phone's observations — this used to be a caveat users had to remember.
- **Compass angles are relative.** The operator sets a 0° reference at their
  first position; later angles are `(heading - reference) mod 360`. Aiming at
  the prop rotates heading degree-for-degree with position, so this needs no
  true-north calibration and cancels constant magnetic bias. iOS exposes
  `webkitCompassHeading` (behind `requestPermission()`, needs a user gesture);
  its `alpha` is RELATIVE and must never be used. Android needs
  `deviceorientationabsolute`, whose alpha runs counter-clockwise.
- **Camera handedness.** `_look_at_R` must use `cross(up, z)` for right and
  `cross(x, z)` for down. The reverse order points "right" to the LEFT and
  silently **mirrors every reconstruction** — an xLights import then comes out
  as a mirror image of the prop. This bug cost 118.6px vs 69.0px of
  reprojection error on real data and was invisible to every other diagnostic.
- **Aim height is measured, not typed.** `_projection_matrix` depends on the
  camera height `h` and the aim height `t` **only through `h - t`** — adding a
  constant to both leaves the rotation identical and merely translates the eye,
  and `_normalize` subtracts the per-axis minimum before export, so a common
  offset costs nothing. So the geometry never needed two numbers, it needed one:

      h - t = distance * tan(depression)

  which the phone measures. `tilt.js` derives the depression from `beta`/`gamma`
  as `asin(cos B * cos G)` — exact against the full W3C rotation matrix,
  independent of `alpha` (so it does not care about the magnetometer), and
  orientation-agnostic, which naive use of `beta` alone is not. The operator
  centres the prop under the crosshair, so **the aim point defines itself**: no
  one needs to know how high the prop's middle is. Both "camera height" and
  "prop centre height" fields are gone. A typed height survives only as the
  fallback for a device with no accelerometer (`pitch_deg = None`).
- **The crosshair is the measurement's definition, not decoration.** The video
  is `object-fit: contain` inside `#cam-wrap`, so the box centre is the image
  centre and a CSS-centred reticle sits on the optical axis with no arithmetic.
  Offsetting one without the other silently redefines the aim point.
- **Drift across a capture is reported, not absorbed.** A coded scan spans
  seconds and every frame is attributed to ONE pose, so a wandering hand smears
  the geometry rather than averaging out. The sensor records peak-to-peak pitch
  and heading across the capture window — which closes at `coded_analyze`, not
  at `scan_complete`, because the operator lowers the phone while analysis
  runs — and warns above 3° of tilt or 8° of heading.
- **The plugin's HTML must not be cached.** FPP serves static assets with
  `max-age=31536000, immutable`. That is fine for query-versioned JS and CSS,
  but a cached HTML page keeps requesting an old `?v=` forever — hours were lost
  re-testing code that had already been replaced. The installer sets `no-store`
  on `*.html`. The sensor header also shows a build tag (`v30`); if it does not
  match what was deployed, nothing else matters.
- **Asset cache busting** via `?v=N` on CSS/JS. Increment on every deploy.
- **WebSocket proxy** through Apache at `/blinkymap-ws → ws://127.0.0.1:8765`
  satisfies FPP's same-origin CSP.
- **HTTPS required** for `getUserMedia` and `DeviceOrientationEvent`.
- **Confidence measures discriminability, not brightness.** The old score was
  `peak/255`, which pegged near 1.0 for anything bright, so the minimum-confidence
  gate never rejected anything. Coded scanning supersedes this, but the legacy
  detector's score is now sparsity x compactness x uniqueness (geometric mean).

## Scanning technique — what actually moves the number
Measured on real four-position scans of the same prop:
- **Angular spread dominates.** Two positions 15° apart contribute almost no
  baseline. One near-duplicate viewpoint took reprojection from 44.3px to
  62.1px. Follow the suggested angle; keep positions ≥60° apart.
- **Angle accuracy beats more scans.** Improving ±25° to ±10° is a 2.6x gain;
  going from 2 to 4 positions at fixed accuracy is only 1.5x. Use the compass.
- **Distance barely matters** if kept consistent — a common error becomes a
  global scale, which normalises away. Height matters even less now: it is
  derived from the measured tilt, so standing higher or lower at one position
  is free. What replaces it is **framing consistency** — the crosshair defines
  the aim point, so framing the prop differently at one position is the error
  that survives. Do not buy a tripod, but do brace your hands: the sensor now
  tells you when a capture was too unsteady to trust.
- **A pile of lights is a pathological test case.** Mutual illumination and
  occlusion dominate everything else. Spread the prop out before drawing
  conclusions about accuracy.
- **Pixel pitch is NOT a known distance.** Pitch is measured along the wire; the
  straight-line 3D distance between consecutive LEDs is only ever ≤ pitch, and
  varies by model. Never use it as ground truth or to calibrate FOV.

## Reprojection error — history on the same prop
Useful for judging whether a change actually helped. Consensus metric (median
error of each pixel's consensus point across all views), not the server's
per-pair figure, which is roughly half.

| state | px |
|---|---|
| one-pixel detector, mirrored camera | 119 |
| coded scan, still mirrored | 119 |
| coded scan, handedness fixed | 69 |
| ... dropping one near-duplicate viewpoint | 44 |
| measured tilt replacing both typed heights | not yet measured on real data |

The middle row is the informative one: **better observations did not move
reprojection at all**, which is what finally isolated the mirrored axis.

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
Work happens on a `claude/*` branch, then merges to `main`. `pluginInfo.json`
pins `branch: "main"`, so anything the FPP Plugin Manager installs comes from
`main` — merge before expecting a fresh install to pick a change up.

BlinkyMap is **not** in FPP's official `pluginList.json`, so it does not appear
in the Plugin Manager UI. Install by cloning into `/home/fpp/media/plugins/blinkymap`
and running `scripts/fpp_install.sh`.

## Known Issues / Open Questions
See https://github.com/wbhartmanii/BlinkyMap/issues for filed issues. Several
older ones are now obsolete — per-pixel found/not-found status and the live
scan view were both overtaken by coded scanning.

Open, in rough priority order:
- **Reprojection still sits around 45-60px** on a good four-position scan. The
  camera model is the remaining suspect: lens distortion is unmodelled, and the
  best-fit FOV (~40-70°, weakly constrained) does not clearly match the
  configured value. A one-time per-device calibration would settle it — no
  browser API exposes the true FOV, and pitch cannot be used to derive it.
- ~~**Camera tilt is unmodelled.**~~ Done, but note *how*. The earlier attempt
  that made things worse bolted a measured rotation ON TOP of a look-at built
  from a typed position — double-counting the tilt and destroying the
  self-consistency look-at has under position error. The shipped version uses
  the same measurement to infer the POSITION instead, leaving look-at untouched:
  the camera still aims exactly at the target and nothing is applied twice.
  `tests/test_tilt_geometry.py` proves the reconstruction is identical to
  ground truth up to a Y translation that export normalises away. **Not yet
  validated on real hardware** — the simulation says exact, a real scan has not
  been taken.
- **Legacy one-pixel scan path** still exists server-side (`start_scan`) but
  nothing drives it. Remove once coded scanning is proven on a real prop.
- Extrapolate positions for unseen pixels from neighbours.
- 2D-only mapping mode option.

## Tests
No CI. Run both before trusting a geometry change:
- `python3 -m pytest tests/test_tilt_geometry.py` — the tilt scheme against the
  shipped `_projection_matrix`, with synthetic ground truth.
- `node tests/test_tilt.mjs` — `tilt.js` against the full W3C rotation matrix.

## Testing Checklist
1. Browse to `https://<fpp-ip>/plugin/blinkymap/` on **both** laptop and phone;
   accept the self-signed cert on each and pick a role.
2. **Confirm the build tag** in the sensor header matches what was deployed. If
   it does not, stop — you are testing stale code.
3. Control tab: FPP IP, start channel, pixel count, detection settings → Save &
   Connect. Watch for "Sensor connected". There is no height field any more.
4. Phone: ⚙ → Open Camera → **Enable Sensors** → Set 0° here. Setup collapses
   once all three are done. Confirm the "Aim" readout shows a tilt angle and a
   derived height — if it says "manual", the accelerometer was refused and a
   typed height is being used instead.
4b. **Centre the prop under the crosshair** and frame it the same way at every
   position. The crosshair defines the aim point the tilt is measured against.
5. Tap **Scan From Here**. A 24-pixel scan takes ~3s (5 frames). Expect the
   string to flash multi-coloured patterns, then circles on every pixel found.
6. **Some pixels reporting "not visible" is correct**, not a regression — that
   is honest occlusion reporting.
7. Follow the suggested next angle. Keep positions ≥60° apart; a second scan is
   required before any 3D model can exist.
8. Control tab: 3D model should appear after the second scan, and the confidence
   tip should name the actual limiting factor.
9. Export tab: download .xmodel and import into xLights Layout.

Quick server-side checks:
- `grep _run_coded_scan /tmp/blinkymap_server.log` — frames and counts per scan.
- `curl -s http://<fpp-ip>/api/testmode` — confirms what FPP is driving.
