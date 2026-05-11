# BlinkyMap

Automatically build a 3D xLights model for a pixel-wrapped tree (or any prop)
by triangulating pixel positions from a single camera moved to multiple spots.

---

## Install & Run

### Option 1 — Download & double-click (no Python needed) ⭐ recommended

1. Go to the [**Releases**](https://github.com/wbhartmanii/blinkymap/releases) page
2. Download the zip for your OS:
   - `BlinkyMap-Windows.zip`
   - `BlinkyMap-Mac.zip`
   - `BlinkyMap-Linux.zip`
3. Unzip → double-click **BlinkyMap** (or `BlinkyMap.exe` on Windows)

> **Windows note:** You may see a SmartScreen warning because the app isn't
> code-signed yet.  Click *More info → Run anyway*.

---

### Option 2 — One-liner with pipx (if you have Python)

```bash
pipx install git+https://github.com/wbhartmanii/blinkymap
blinkymap
```

[pipx](https://pipx.pypa.io) installs Python apps in isolated environments.
Install it once with `pip install pipx` (or `brew install pipx` on Mac).

---

### Option 3 — Plain pip

```bash
pip install git+https://github.com/wbhartmanii/blinkymap
blinkymap
# or: python -m blinkymap
```

---

### Option 4 — Run from source

```bash
git clone https://github.com/wbhartmanii/blinkymap
cd blinkymap
pip install -r requirements.txt
python main.py
```

---

## How it works

1. **Controller tab** — enter your FPP / controller IP, universe, pixel count.
2. **Camera tab** — open one camera (USB webcam or phone via IP/RTSP), set FOV.
3. **Sessions tab** — run as many scan passes as you like:
   - BlinkyMap fires every pixel one at a time via E1.31/sACN.
   - The camera watches and records where each lit pixel appears.
   - Move the camera to another spot around the tree → run another session.
   - After 2 sessions, a 3D model appears.  Keep adding sessions until the
     confidence score is high enough (aim for **Good** or **Excellent**).
4. **Export tab** — save `.xmodel` + CSV + PLY when you're happy.

### Why multiple sessions?

Pixels hidden behind the trunk won't be visible from one angle.  Walking
around the tree and running 3–4 sessions gives BlinkyMap enough viewpoints
to triangulate every pixel. Session 3 onward can **auto-locate** the camera
using already-mapped pixels (no measuring needed).

### Confidence display

| Colour | Meaning |
|--------|---------|
| 🟢 Green dot, tiny halo | High confidence — good triangulation |
| 🟡 Yellow, small halo | Medium confidence |
| 🔴 Red, large fuzzy halo | Low confidence — try another session from that side |
| ⬜ Grey / listed only | Pixel not seen yet — might be behind trunk or dead |

The pixel status list (right panel) shows every channel sorted worst-first so
you always know exactly which pixels still need attention.

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
- Increase **capture delay** in the Controller tab if your camera is slow to
  adjust exposure (0.2–0.3 s is usually enough).
- **IP cameras / phones:** enter the RTSP URL
  (`rtsp://user:pass@192.168.x.x/stream`).
  Android: *IP Webcam* app.  iPhone: *EpocCam* or *Camo*.
- More sessions from more angles = higher confidence.

---

## Building from source (for contributors)

```bash
pip install pyinstaller
pyinstaller BlinkyMap.spec --noconfirm
# output is in dist/BlinkyMap/
```

GitHub Actions runs this automatically on every push to `main` and attaches
the zips to every GitHub Release.
