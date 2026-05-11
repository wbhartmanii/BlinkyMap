# BlinkyMap

Automatically build a 3D xLights model for a pixel-wrapped tree (or any prop)
by triangulating pixel positions from a single camera moved to multiple spots.

---

## Install & Run

### Option 1 — FPP Plugin (no extra hardware needed) ⭐ recommended

If you already run **Falcon Player (FPP)** on a Raspberry Pi, this is the easiest
path — your phone browser is the camera, no laptop required.

1. In the FPP web UI go to **Content Setup → Plugin Manager**
2. Enter the plugin URL:
   ```
   https://github.com/wbhartmanii/blinkymap
   ```
3. Click **Install** — FPP runs `install.sh` which pulls the Python deps and
   vendors Three.js locally (needs internet once)
4. The **BlinkyMap** menu item appears in FPP's navigation
5. Open `http://<pi-ip>/plugin/blinkymap/` on your **phone** — that's the whole UI

> The plugin fires pixels via E1.31 to localhost (zero extra wiring), and your
> phone's browser handles the camera and 3D viewer via WebRTC + Three.js.

---

### Option 2 — Download & double-click (standalone desktop app)

1. Go to the [**Releases**](https://github.com/wbhartmanii/blinkymap/releases) page
2. Download the zip for your OS:
   - `BlinkyMap-Windows.zip`
   - `BlinkyMap-Mac.zip`
   - `BlinkyMap-Linux.zip`
3. Unzip → double-click **BlinkyMap** (or `BlinkyMap.exe` on Windows)

> **Windows note:** You may see a SmartScreen warning because the app isn't
> code-signed yet. Click *More info → Run anyway*.

---

### Option 3 — One-liner with pipx

```bash
pipx install git+https://github.com/wbhartmanii/blinkymap
blinkymap
```

[pipx](https://pipx.pypa.io) installs Python apps in isolated environments.
Install it once with `pip install pipx` (or `brew install pipx` on Mac).

---

### Option 4 — Plain pip

```bash
pip install git+https://github.com/wbhartmanii/blinkymap
blinkymap
# or: python -m blinkymap
```

---

### Option 5 — Run from source

```bash
git clone https://github.com/wbhartmanii/blinkymap
cd blinkymap
pip install -r requirements.txt
python main.py
```

---

## How it works

### FPP Plugin (phone browser)

1. **Setup** — enter your controller IP, universe, start channel, and pixel count.
   Open your phone camera and set the field of view.
2. **Scan** — stand in front of the tree, enter your angle/distance/height, hit
   **Start Session**. BlinkyMap fires every pixel one at a time via E1.31; your
   phone detects each lit pixel in the camera frame automatically.
3. **Move & repeat** — after the scan finishes, BlinkyMap tells you exactly
   where to stand next (see *Smart next-position suggestion* below). Move there
   and run another session. After 2 sessions a live 3D model appears.
4. **Export** — when confidence is Good or Excellent, download
   `.xmodel` + CSV straight to your phone.

### Desktop app

1. **Controller tab** — enter your FPP / controller IP, universe, pixel count.
2. **Camera tab** — open a USB webcam or phone via IP/RTSP, set FOV.
3. **Sessions tab** — run scan passes; move the camera between each one.
4. **Export tab** — save `.xmodel` + CSV + PLY when confidence is high enough.

---

## Smart next-position suggestion

After every completed session BlinkyMap scores every possible camera angle and
recommends the single best spot to stand next (height stays the same).

Three factors are weighted:

| Factor | Weight | What it means |
|--------|--------|---------------|
| Angular gap | 30 % | Prefer angles far from sessions you've already done |
| Coverage | 50 % | Face the side of the tree with the fewest detected pixels — that's where unseen pixels are hiding behind the trunk |
| Spread | 20 % | Maximise the triangulation baseline for low-confidence pixels |

The suggestion appears as a card in the Scan tab showing the recommended angle,
same distance, and a one-line explanation. Tap **Use This Angle** to pre-fill
the form. In the 3D view a glowing marker shows exactly where to stand.

---

## Why multiple sessions?

Pixels hidden behind the trunk won't be visible from one angle. Walking around
the tree and running 3–4 sessions gives BlinkyMap enough viewpoints to
triangulate every pixel. The smart suggestion tells you the highest-value spot
each time so you get a Good model in the fewest possible moves.

### Confidence display

| Colour | Meaning |
|--------|---------|
| 🟢 Green dot, tiny halo | High confidence — solid triangulation |
| 🟡 Yellow, small halo | Medium confidence |
| 🔴 Red, large fuzzy halo | Low confidence — try another session from that side |
| ⬜ Grey / listed only | Pixel not seen yet — might be behind trunk or dead |

The pixel list (right panel / Model tab) is sorted worst-first so you always
know which pixels still need attention.

---

## xLights import

After exporting:

1. Open xLights → **Layout** tab.
2. Right-click in the model list → **Import Model** → select `BlinkyTree.xmodel`.
3. The model uses a cylindrical-unwrap grid: rows = height, columns = angle around
   the tree, so Meteor Shower, Pinwheel, spirals etc. all work intuitively.

The `BlinkyTree.csv` (`Channel, X, Y, Z`) can also be imported via
**Custom Model Creator** if you want to rebuild the model manually.

---

## Supported controllers

Any device that listens for E1.31/sACN on UDP port 5568:

- FPP (Falcon Player) — auto-detected via REST API
- Falcon F16v3, F48, F4V3
- Kulp K8 / K16 / K24 / K32
- HinksPix PRO
- xLights E1.31 bridge output
- Virtually any modern pixel controller with E1.31 input

---

## Camera tips

- Darker room = better LED detection (turn off overhead lights if possible).
- Increase **capture delay** if your camera is slow to adjust exposure
  (0.2–0.3 s is usually enough).
- **IP cameras / phones (desktop app):** enter the RTSP URL
  (`rtsp://user:pass@192.168.x.x/stream`).
  Android: *IP Webcam* app. iPhone: *EpocCam* or *Camo*.
- More sessions from more angles = higher confidence. Follow the suggestion!

---

## Building from source (for contributors)

```bash
pip install pyinstaller
pyinstaller BlinkyMap.spec --noconfirm
# output is in dist/BlinkyMap/
```

GitHub Actions runs this automatically on every push to `main` and attaches
the zips to every GitHub Release.
