# BlinkyMap

**Automatically build a 3D xLights model for any pixel-wrapped prop** — trees,
arches, candy canes, megatrees — by triangulating pixel positions from a phone
camera moved to a few spots around the prop.

No special hardware. No tape measure. Just walk around with your phone, run
3–4 scans, and get a complete `.xmodel` ready to drop into xLights.

---

## How it works

BlinkyMap fires every pixel one at a time through your controller and uses your
phone's camera to detect where each pixel appears in the frame. After 2+
scans from different angles it triangulates every pixel's real-world (X, Y, Z)
position and builds a 3D model.

1. **Setup** — enter your FPP controller IP, start channel, and pixel count.
   Open your phone camera and set the horizontal field of view.
2. **Scan** — stand in front of the prop, enter your angle/distance/height, hit
   **Start Session**. BlinkyMap lights every pixel one at a time; your phone
   detects each one automatically.
3. **Move & repeat** — BlinkyMap tells you the best spot to stand next to
   maximise coverage. Move there and run another session. After 2 sessions a
   live 3D model appears in the Model tab.
4. **Export** — when confidence is Good or Excellent, download `.xmodel` + CSV
   straight to your phone.

---

## Install (FPP Plugin)

BlinkyMap runs as an **FPP (Falcon Player) plugin** — your phone browser is the
camera, no laptop or extra software required.

**Compatibility:** FPP 6.0+ · Raspberry Pi OS Bullseye/Bookworm · Debian 11/12

1. In the FPP web UI go to **Content Setup → Plugin Manager**
2. Paste the plugin info URL:
   ```
   https://raw.githubusercontent.com/wbhartmanii/BlinkyMap/main/pluginInfo.json
   ```
3. Click **Install** — the installer automatically:
   - Installs Python deps (`numpy`, `websockets`, `requests`) via apt
   - Downloads and vendors Three.js locally (internet needed once)
   - Configures Apache with the plugin alias and WebSocket proxy
   - Generates a self-signed HTTPS certificate for camera access
4. The **BlinkyMap** entry appears in FPP's navigation menu

**Open BlinkyMap using HTTPS** (required for camera access):

```
https://<your-fpp-ip>/plugin/blinkymap/
```

Your browser will warn about the self-signed certificate — click
**Advanced → Proceed** once. After that, the camera works in any browser on
any device on your local network.

> See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if something doesn't come up.

---

## Smart next-position suggestion

After each scan, BlinkyMap scores every possible camera angle and recommends
the single best spot to stand next.

| Factor | Weight | What it means |
|--------|--------|---------------|
| Coverage | 50% | Face the side with the fewest detected pixels — that's where pixels are hiding |
| Angular gap | 30% | Prefer angles far from scans you've already done |
| Spread | 20% | Maximise the triangulation baseline for low-confidence pixels |

The recommendation appears as a card in the Scan tab. Tap **Use This Angle**
to pre-fill the form.

---

## Why multiple scans?

Pixels on the back of a prop won't be visible from one angle. Walking around
and running 3–4 scans gives BlinkyMap enough viewpoints to triangulate every
pixel. The smart suggestion finds the highest-value spot each time so you
reach a good model in the fewest moves.

### Confidence display

| Grade | Meaning |
|-------|---------|
| 🟢 Excellent | Solid triangulation from multiple angles — ready to export |
| 🟡 Good | Usable model; a couple more scans will improve accuracy |
| 🟠 Fair | Limited coverage — scan from more angles |
| 🔴 Poor | Not enough data yet — need at least 2 sessions from different sides |

---

## xLights import

1. Open xLights → **Layout** tab.
2. Right-click in the model list → **Import Model** → select `BlinkyMap.xmodel`.
3. The model uses a cylindrical-unwrap grid: rows = height, columns = angle
   around the prop — Meteor Shower, Pinwheel, spirals all work intuitively.

The `BlinkyMap.csv` (`Channel, X, Y, Z`) can also be imported via
**Custom Model Creator** if you want to build the model manually.

---

## Supported controllers

BlinkyMap talks to FPP via its REST API (with multisync support for remotes).
Any controller FPP manages — wired or wireless — works automatically:

- Falcon F16v3, F48, F4V3
- Kulp K8 / K16 / K24 / K32
- HinksPix PRO
- Any FPP-connected E1.31/sACN pixel controller

---

## Camera tips

- **Lower ambient light = better detection.** Indoors: turn off overhead lights.
  Outdoors: scan at dusk or after dark.
- **Slow exposure?** Increase capture delay to 0.2–0.3 s in Setup.
- **Confidence too low?** Raise the minimum detection confidence slider — this
  reduces false positives from ambient/reflected light.
- **More angles = higher confidence.** Follow the suggestion after each scan.

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for solutions to common issues
including camera errors, WebSocket connection problems, and plugin 404s.

---

## Contributing

Bug reports and PRs welcome. See the
[open issues](https://github.com/wbhartmanii/BlinkyMap/issues) for known bugs
and the roadmap.

**Dev branch:** all work goes on `claude/blinkymap-3d-modeling-J34d4`, then
merges to `main`.

To run the server locally for development:

```bash
git clone https://github.com/wbhartmanii/BlinkyMap
cd BlinkyMap
pip install numpy websockets requests
python blinkymap_server.py
# then open www/blinkymap/index.html in a browser
```

---

## License

[MIT](LICENSE) — © 2026 wbhartmanii
