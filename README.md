# BlinkyMap

Automatically build a 3D xLights model for a pixel-wrapped tree (or any prop)
by triangulating pixel positions from two or more cameras.

## How it works

1. **Connect** BlinkyMap to your FPP/E1.31 controller.
2. **Place** two (or more) cameras pointing at your tree from different angles.
3. **Calibrate** — enter the distance between the cameras and their FOV,
   or use checkerboard calibration for higher accuracy.
4. **Scan** — BlinkyMap fires each pixel one at a time while the cameras watch.
   OpenCV detects the lit pixel in each frame and records its 2D position.
5. **Triangulate** — BlinkyMap computes the 3D position of every pixel.
6. **Export** — get a `.xmodel` file you can import directly into xLights
   plus a `Channel,X,Y,Z` CSV for the Custom Model Creator.

## Requirements

- Python 3.8+
- Two or more cameras (USB webcams and/or IP/RTSP cameras)
- Controller reachable via E1.31/sACN (FPP, Falcon, Kulp, etc.)

## Install

```bash
pip install -r requirements.txt
python main.py
```

## Calibration tips

### Simple mode (good enough for most trees)
- Place cameras at the same height, one left and one right.
- Measure the horizontal distance between them (a tape measure works).
- Enter that as the **baseline**.
- Enter your camera's horizontal FOV (check the spec sheet, or use ~70° for
  a typical webcam).

### Checkerboard mode (more accurate)
- Print a checkerboard pattern (asymmetric, e.g. 9×6 inner corners).
- With both cameras running, move the board to 10+ different positions
  and click **Capture Pair** each time.
- Click **Apply Calibration** — OpenCV computes precise intrinsics and the
  stereo baseline automatically.

## xLights import

After exporting:
1. Open xLights → Layout tab.
2. Right-click in the model list → **Import Model** → select `BlinkyTree.xmodel`.
3. The model uses a cylindrical-unwrap grid so standard effects (Meteor Shower,
   Pinwheel, etc.) work intuitively — rows = height, columns = angle around the tree.

The `BlinkyTree.csv` (Channel, X, Y, Z) can also be imported via the
**Custom Model Creator** dialog if you prefer to rebuild the model manually.

## Supported controllers

Any device that listens for E1.31/sACN on UDP port 5568, including:
- FPP (Falcon Player)
- Falcon F16v3/F48
- Kulp K8/K16/K24/K32
- xLights E1.31 bridge output
- Most commercial pixel controllers

## Camera tips

- Darker environment = better LED detection (turn off room lights if possible).
- Use the **capture delay** slider to give slower cameras time to stabilise.
- IP cameras: enter the RTSP URL (`rtsp://user:pass@192.168.x.x/stream`).
  Many phone camera apps (IP Webcam for Android, EpocCam, etc.) work well.
- More cameras = more accurate triangulation (supports 2+).
